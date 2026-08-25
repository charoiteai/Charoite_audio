---
layout: post
title: "A mechanical move, verified mechanically"
date: 2026-08-25 21:00:00 +0300
---

Charoite is in an overhaul phase: no new features for a while, just carving
the codebase into pieces that can be reasoned about. The rhythm is one or two
small batches a day, each merged through the usual review circle. This week
that produced a lock helper, a single config-fallback loader, a single
resolver for an external CLI the pipeline shells out to — and the first real
aggregate extraction.

The live transcript used to be a region inside `main.py`: the structure that
accumulates recognized speech for both audio channels, merges overlaps, cuts
repeated fragments, and renders the text every downstream consumer reads.
Everything else in the pipeline imports it. Moving it is the definition of a
scary refactor: 243 lines that must land in a new module byte-for-byte
equivalent, or a week later some meeting note quietly renders wrong.

The move itself was done by a coding agent running in a parallel session,
with instructions to relocate, not to improve. The interesting question is
how you *check* that. Reading the diff proves nothing: a 250-line block that
moved between files is exactly the diff a human rubber-stamps. Green tests
prove less than you'd hope — a subtle behaviour change in text merging can
pass every existing test and still corrupt output on real speech.

So the check was mechanical too:

- Before the move: characterization tests written against the *old* code,
  168 lines of them, pinning current behaviour — including the ugly cases
  (overlap cut across chunk boundaries, a channel going silent mid-merge).
- After the move: parse both versions of the relocated functions and compare
  their ASTs. Not the text, the syntax tree — whitespace and import order
  may differ, logic may not.
- Then the full suite, all 1153 tests, on top.

The AST comparison is the part I'd keep from this week. It converts "trust
the agent's diff" into "the transformation is identity, checked by a
program". `main.py` went from 365 lines to 122, the aggregate now lives in
its own `transcript.py` with its own tests, and nobody had to *believe*
anything.

Delegating mechanical work is fine. Delegating the verification of
mechanical work is not — that part should not run on trust at all.
