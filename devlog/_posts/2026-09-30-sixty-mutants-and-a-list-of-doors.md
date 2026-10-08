---
layout: post
title: "Sixty mutants and a list of doors"
date: 2026-09-30 23:40:00 +0300
---

Mutation testing is only useful if its verdict is cheap enough to run before
every merge and strict enough to mean something. A whole-file pass over our
code is thousands of mutants and hours, so the checker judged a range of changed
lines, and a big range took hours. Today's change replaces the range with a
sample.

The sample is deterministic. A mutant's identity is its path, qualified name,
description, canonical node text and its ordinal among twins in the file — no
line numbers. The canonical text comes from our own walk of the syntax tree,
not `ast.dump`, which differs between Python 3.12 and 3.13+. Sort the identities
by their sha256 and take the first 60. No seed, so CI and a local run judge the
same set, and a test-only commit or a shifted line keeps it. Critical mutants
come first; the rest fill the sample, with a floor of 15 and round-robin over
files.

"Critical" is the second half. A door is a module or function where something
irreversible happens: audio written to disk, data leaving the machine, owner
data written or deleted. They live in `docs/design/layout.json` under
`mutation_critical`, and the layout check requires a decision for every module
that imports a network library or a safe-write helper — a new door cannot be
forgotten silently. A surviving mutant in a door turns the verdict red and
holds the merge. Survivors elsewhere are listed, the verdict stays green.
Runs are resumable too: a journal of judged mutants, `--resume KEY`, and a
parent that on SIGINT or SIGTERM prints how many of how many were judged and
exits 128 plus the signal.

The first real use came within hours. A fix for merging voice shards in an
in-room recording left 10 survivors, all in one file. The follow-up closed six
with tests and one simplification: the merge threshold is inclusive in both
linkage modes, the veto is strictly below its constant, and a missing pair
defaulted to 1.0 although it could not happen — the default was removed. Two
survivors are equivalent (`-1` and `0` both mean "auto speakers"; `flush=True`
on a log line) and stay. One more detail: restoring the original line order of a
loop made its unchanged lines stop being mutated as "changed".

Twenty refutation experiments on the sampler itself — each fix removed from the
committed file — turned a test red every time. That is the rule I trust most:
a test is believed once it has been seen failing.
