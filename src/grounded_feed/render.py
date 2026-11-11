"""Readable renderer: admitted blocks -> one chronological panel text.

Model surfaces are never rewritten: the caller's `block.text` bytes are emitted
verbatim (tests assert that). Only non-model framing is added: a topic label
once per topic block and each answer under its question, in block order. This is
the product-facing replacement for `MeetingThread._render_feed`, whose output is
the string carried by the existing NDJSON `thread` event.

Explicit chronology contract: every answer is rendered directly under its
question, in arrival order, even if a non-model event sits between them in source
order; such events are published after the Q/A pair (they are never moved into
the model block and the pair stays readable). Blocks themselves are never
reordered on the canvas.
"""

from __future__ import annotations


def topic_labels(blocks) -> dict[str, str]:
    return {b.topic_id: b.topic_label for b in blocks if b.topic_id}


def render_blocks(blocks) -> str:
    ordered = sorted(blocks, key=lambda b: (b.position, b.system_after, b.serial))
    question_ids = {b.question_id for b in ordered if b.kind == "question"}
    answers: dict[str, list] = {}
    for block in ordered:
        if block.kind == "answer":
            answers.setdefault(block.question_id or "", []).append(block)
    for group in answers.values():
        group.sort(key=lambda block: block.serial)
    parts: list[str] = []
    shown_topic = ""
    for block in ordered:
        if block.kind == "system":
            parts.append(block.text)
            continue
        if block.kind == "answer":
            if block.question_id in question_ids:
                continue  # rendered under its question, in answer order
            parts.append(block.text)  # defensive: an orphan answer stays visible
            continue
        if block.topic_id and block.topic_id != shown_topic:
            parts.append(block.topic_label)
            shown_topic = block.topic_id
        if block.kind == "paragraph":
            parts.append(block.text)
        elif block.kind == "question":
            parts.append(block.text)
            for answer in answers.get(block.question_id or "", ()):
                parts.append(answer.text)
        else:  # pragma: no cover - canvas validates kinds
            raise ValueError("unknown block kind")
    return "\n\n".join(parts)


def assert_surface_preserved(blocks, text: str) -> None:
    """Every model surface must literally survive in the rendered panel text."""
    for block in blocks:
        if block.kind == "system":
            continue
        if block.text not in text:
            raise AssertionError("rendered text rewrote a model surface")
