---
layout: post
title: "The bench measured a path the daemon never takes"
date: 2026-10-05 18:10:00 +0300
---

Memory in Charoite is a search over the meeting graph that feeds an answer, the
live context and topic expansion. I had a bench for it: a set of 37 questions
with known facts, and a score of how many come back. The score was 29 of 37. It
was also not what users get.

Each consumer in the daemon read memory its own way, with its own limit, snippet
size, embed timeout and budget, and the bench used none of them. It called
retrieval directly and counted a fact found if the search returned it. The
daemon then packs results into a prompt with a budget, and what does not fit is
cut. The bench never saw the cut.

Today every consumer reads memory through one profile in `brain.py`: a record
holding the numbers (limit, snippet size, timeout, budget, synthesis settings),
with `search(profile, …)` and `pack(profile, …)` as the only seam. The old
private primitives are guarded by the layout gate, and `search` without a
config is an error rather than an empty answer — the daemon swallows memory
errors, so an empty config would silently turn memory off. A characterization
test pins the three consumers' blocks byte for byte; I wrote it on the old code
before moving anything.

The bench takes `--profile` and counts a fact only if it is in the packed body
or nodes. A miss gets a name: cut by budget, not returned, or search empty. Same
questions, no model, fixed hash seed: the old contour 29 of 37, the answer
profile 10 of 37. All 19 differing questions are the same shape — found by
search, then lost. Seventeen were cut by the 2000-character answer budget,
because dossiers go first and take it; two were not returned at all. That is
the daemon's real behaviour. It was always like this; now it is visible, and the
budget is its own piece of work.

The bench also became a watch. Results append to a journal, an accepted baseline
is a deliberate step (a worse one needs a reason, a fallback-model run is
refused), and two or more questions flipping from found to lost with the same
semantic-search use write an alert file that the morning brief shows. A run
without a baseline, or with fewer than half the questions comparable, is shown
as "watch not armed" rather than as green. Retrieval depends on hash order, so
the bench re-runs itself with `PYTHONHASHSEED=0`.

Every guard was checked by putting the old or broken behaviour into the
committed file: 28 substitutions, all red. The number worth keeping is not 29
or 10. It is the gap between them, which only exists once the bench walks the
same path as the product.
