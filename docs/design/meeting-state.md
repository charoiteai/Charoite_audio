# MeetingState — architecture for the live thread

***English** · [Русский](../ru/design/meeting-state.md)*

Status: design (October 2026). Prototype №689 showed that a pure
incremental prompt does not keep structure on meetings longer than
~20 minutes. This document describes the mechanisms that replace it.

## Goal

During a meeting the user sees a short side view (≤ 1500 characters):

- current topic (one line),
- at most three open items that need attention,
- past topics collapsed to “topic → outcome”.

The model only proposes additions. Rules (topic change, closing,
question–answer linking, length limits) live in code.

## State

```python
@dataclass
class Action:
    text: str
    owner: str | None = None
    due: str | None = None

@dataclass
class Question:
    id: str
    text: str
    asked_by: str | None
    topic_id: str | None
    status: Literal["open", "answered"] = "open"
    answered_fragment: str | None = None
    created_at: float = field(default_factory=time.time)

@dataclass
class Topic:
    id: str
    title: str
    status: Literal["open", "closed"] = "open"
    summary: str = ""                 # 1–3 lines after closing
    decisions: list[str] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    closed_at: float | None = None

@dataclass
class AttentionItem:
    text: str
    kind: Literal["question", "decision", "action", "risk"]
    source_id: str | None = None

@dataclass
class MeetingState:
    stamp: str
    topics: list[Topic] = field(default_factory=list)
    open_questions: list[Question] = field(default_factory=list)
    attention: list[AttentionItem] = field(default_factory=list)  # ≤ 3
    current_topic_id: str | None = None
    last_update_at: float = field(default_factory=time.time)
```

The state lives in the daemon’s memory. Only the screen view (and a
final snapshot on stop) is written to disk.

## Update cycle (~every 2 minutes)

1. Clean the new fragment (echo filter + noise / joke filter).
2. Topic switch? Compare embeddings of the current topic and the new
   fragment. If similarity drops below a calibrated threshold, optionally
   confirm with a light model call, then close the current topic and
   open a new one.
3. Question linking. Match the fragment against `open_questions`
   (embedding + NLI). If it answers an existing question, mark it
   answered. Otherwise extract new questions.
4. Update the current topic. The model receives only the current topic,
   its open questions, and the new fragment. It returns only additions
   (new decisions, actions, notes). Code applies them; nothing is
   deleted or rewritten.
5. Recompute `attention` (hard cap of 3).
6. Emit `screen_view()` (≤ 1500 characters).

## Key mechanisms

### Topic switch

Embedding cosine between the current topic (title + short summary) and
the new fragment. Threshold is calibrated on real meetings. An optional
light confirmation call reduces false switches.

### Question–answer linking

Open questions are first-class objects with ids. A new fragment is
matched against them before any new extraction. NLI (when available)
confirms that the fragment actually answers the question.

### Closing a topic

A topic closes when the detector says the conversation has moved on, or
when it has not been updated for N minutes. On close the model is asked
to collapse it into 1–3 lines (“topic, outcome, who does what”).

### Additions only

The model is instructed to return only new items. Code never deletes or
overwrites existing decisions, actions or questions from a model reply.

## Screen view

```
**Now:** <current topic one-liner>
**Attention:**
- <item 1>
- <item 2>
- <item 3>
**Past:**
- <topic> → <outcome>
- …
```

Hard limit: 1500 characters. Full detail is available on demand or after
the meeting ends.

## Integration points

| Place | Change |
|-------|--------|
| `daemon.py` | New background loop (below live-hint priority). Reads the transcript tail, calls `MeetingState.update()`. Yields to `live_gate`. |
| `transcript.py` | Unchanged. MeetingState reads speech via `speech_of()`. |
| Screen | Writes `screen_view()` into the existing hint area or a new sidecar. |
| Stop | Saves a full snapshot for the archive. |

## Calibration and risks

| Parameter | How to set |
|-----------|------------|
| Topic-switch threshold | Measure on 3–5 real meetings. Watch for merging vs over-splitting. |
| Cycle interval | Start at 2 min. Increase if the model is frequently busy. |
| Attention cap | Hard 3. |
| Echo | Existing text-based echo detector runs before embedding. |

Risks: false topic switches (threshold + optional confirm), missed
question links (NLI as second filter), residual hallucinations (strict
JSON + additions-only + pre-cleaning).

## Minimal path

1. Implement `MeetingState` + `screen_view()` with stubs.
2. Add the embedding-based topic detector.
3. Wire the loop into the daemon (screen view only).
4. Add update extraction and question linking.
5. Calibrate thresholds on real meetings.
6. Enable topic collapse.

## Relation to existing code

- Embeddings: bge-m3 (already used for graph search and déjà vu).
- NLI: optional model in `models/nli/` (already used for thesis dedup).
- Echo filter: existing text-based detector.
- Priority: the loop must yield to live hints and the decision-gate
  shadow, same as other background work.

---

*Design note, October 2026. Intended for implementation after the
№689 prototype measurements.*
