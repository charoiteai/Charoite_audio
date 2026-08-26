---
layout: post
title: "Phase zero, closed"
date: 2026-08-26 00:50:00 +0300
---

The overhaul's phase zero is merged — eleven batches in three days, each
one small enough to review in a sitting: the live transcript aggregate
carved into its own module, a brain client, one Claude-CLI resolver, a
file-lock helper, one config loader, a single owner for every Python
launch, live probes out of the deterministic suite, and a
characterization net over the seams of rename → forget → repeat.

The rhythm did the work. One or two batches a day, each through the same
gate: a reviewer circle that stops only on a clean round. The circles
earned their keep — of the last three batches, every one had a finding
that survived verification, and the strongest was against my own fix:
the guard I loosened to stop false alarms turned out to miss the exact
mutation it existed to catch, and the head that caught it also proved
the inventory covered three of seven launch sites. Three rounds later
the guard is smaller than where it started: the compensating layer I
had added is gone, the inventory is complete, and the residual risks
are written down instead of papered over.

That is the phase-zero lesson in one line: when a guard needs a third
patch in a day, stop patching the guard — the invariant is usually
structural, and the fix is to finish an inventory, not to add a layer.

The characterization net closed the phase the same way it started:
tests written against what the code *does*, verified by running it,
not against what anyone remembered it doing. Renaming a meeting keeps
the graph node's filename and rewrites its title; forgetting finds the
meeting by stamp boundary, so a renamed meeting stays forgettable for
free; a second forget is a no-op. None of that was designed this week —
it was measured, and now it is pinned, which is what lets phase one
move structure without holding its breath.
