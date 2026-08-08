# Local-first, not local-only. Why a strong model runs the graph

*[**English**] · [Русский](docs/ru/MANIFESTO.md) · [中文](docs/zh/MANIFESTO.md)*

This is the honest takeaway from several months of running Charoite on
real meetings. In short: local models already hear, search and classify
beautifully — but they cannot maintain a knowledge graph yet. We
measured it, accepted it, and built the architecture around that fact
rather than against it.

## Where we started

The product promise is simple: conversations never leave the machine.
Speech recognition, speaker separation, hints, archive search — all on
the laptop, not a single network call leaving it. We honestly tried to
push that bar all the way: to have local models also run the knowledge
graph end to end — people and system nodes, decision chronicles, topic
dossiers.

It did not work out. And the boundary is worth describing precisely,
because it does not run where internet arguments usually place it.

## What local models do well — numbers, not opinions

Everything below runs on an ordinary laptop, fully offline:

- **Speech recognition.** GigaAM transcribes Russian near-verbatim —
  control phrases come back whole, with at most a rare proper noun
  lost. English and Chinese run on Parakeet and Whisper, same local
  story.
- **Speaker separation.** pyannote segmentation plus ERes2Net
  embeddings. On our benchmark the diarization error rate dropped from
  0.725 to 0.246 — a number from a frozen audio set, not a feeling.
- **Narrow decisions.** A 4–9 GB model answers questions like "does
  this need a web search" or "is this a medical document" at ~92% in a
  third of a second. Cheap, instant, no cloud.
- **Archive search.** One embedder, bge-m3 (93% Top-1 on our corpus),
  fused with lexical search through RRF. Semantics closes the
  vocabulary gap, lexical matching catches internal jargon.
- **Live theses and hints.** A 35B MoE model produces ~27 tokens per
  second on an M1 Max (measured 08.08.2026, `num_ctx: 8192`) — several
  times faster than speech, so it listens to a meeting and prompts as it
  goes.

None of this is a compromise. On these tasks local models are not worse
than cloud ones — they are sufficient, and privacy plus zero latency
come free.

## Where they break

The knowledge graph is a different class of work. Merge two duplicate
nodes of the same person. Notice that April decided X and June decided
the opposite, and both facts sit in the chronicle as if nothing
happened. See that one topic has smeared across three dossiers with no
core anywhere. Append a decision chronicle with an exact provenance
quote and invent nothing.

We asked local models to do this seriously and more than once. The
result is stable: either empty edits or confidently wrong ones. Not
"slightly worse" — work that cannot be accepted, because a wrong graph
edit corrupts memory permanently and silently.

For contrast: one overnight revision by a strong cloud model across 74
graph cores produced an eight-thousand-character report — contradictions,
stale facts, merge candidates, lost threads, three genuine risks. All
with quotes, all verifiable. Local models do not hold this class of
task at all — not just at the edges.

## Why this is structural, not "wait six months"

The difference between the tasks is the size of the answer space and
the length of the horizon.

A narrow task has a small space: yes or no, one of five labels. An
error is cheap and gets caught by the next stage of the pipeline. That
is exactly why our 4 GB classifier holds 92% — the task fits the model.

Graph maintenance is a long horizon: hundreds of files, cross-links,
dozens of simultaneous constraints ("don't touch other sections",
"every fact needs provenance", "invent nothing") that must be held at
once. Here small models fall apart not from lack of knowledge but from
lack of working memory and rule-following discipline. There is a reason the
industry grew dedicated long-memory benchmarks (LoCoMo, LongMemEval):
memory consolidation is not solved by pointing a model at it — it gets
measured and scaffolded separately.

New local models ship every month, and the gap on narrow tasks has
already closed. The gap on synthesis has not. We say "yet" — knowing
that this "yet" is a property of the task class, not of the release
calendar.

## What we built in response

Work is divided by task class, not by ideology.

**Local, always:** audio, recognition, voices, theses, minutes,
classification, search, node extraction from a fresh meeting. This is
the conveyor — it runs every day and asks nobody's permission.

**Strong model, strictly opt-in:** core revision, cross-checking
minutes against the transcript, dossier synthesis. These are rare,
expensive, smart operations — once a night, not once a minute.

And the key part — engineering of distrust around the cloud seam:

- one decision point (`src/privacy.py`), fail-closed: any ambiguity in
  the config reads as "no";
- a kill switch in one environment variable silences all cloud at once;
- the cloud sees a prepared set of files, not the disk: no transcripts
  of other meetings, no recordings, no config, no git history;
- the right to edit the graph is a separate toggle, off by default —
  with a graph backup, edit boundaries and a timeout;
- the API key is always scrubbed from the environment: only the
  subscription CLI works, per-token billing does not exist here;
- every rule is pinned by a structural test that reads the sources and
  fails on any attempt to weaken it.

Privacy here is a property of the architecture, not a paragraph on a
landing page.

## What would change our mind

A manifesto without a refutation criterion is an advertisement. Ours:
we keep a graph-revision bench (reference questions over the archive
plus edit verification) and run every notable local model through it.
The day a local model passes it at cloud level, the cloud seam turns
off with one line of config — the code is already built that way.

Until that day we say it plainly: local-first means "local by default
and privacy by construction". It does not mean "never any cloud" — it
means the boundary is drawn explicitly, guarded by tests, and visible
to anyone in the sources.
