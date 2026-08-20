---
layout: post
title: "A green test for an event nobody sends"
date: 2026-08-19 21:30:00 +0300
---

Stopping a recording is not an action, it is a wait. The daemon has to flush
audio, run the post-meeting pipeline and release its lock, and the app must not
open a new meeting until the old process is actually gone. In Charoite that
wait used to live in five scattered flags on one service object, which is how a
daemon surviving `SIGKILL` could leave the app in "stopping" forever, with the
Stop button doing nothing at all.

Today that turned into one pure type. `ShutdownMachine` has phases (`idle`,
`waitingDaemon`, `stuck`, `done`), events (Stop pressed, daemon exited, poll
tick, kill timeout) and actions (close the capture, poll again, report, force
kill, finish). It holds no reference to the service, so every arc is testable
without a running process. Timings: `terminate()` at 8 seconds, `SIGKILL` at
12, a backup timer at 13, then polling twice a second; after 30 waits the phase
becomes `stuck`, the app says so in plain words, and a second press of Stop is
a request to force-kill rather than a no-op.

The interesting part is not the machine. It is the defect that appeared twice
in a row while building it.

## The same bug, twice

Round one of review found that `killTimeout` was declared in the machine,
covered by a green test, and never sent by the service. The safety timer called
its own code path directly and the machine never heard about it. Fixed it, ran
round two — and the reviewer found `daemonExited` in exactly the same state:
declared, tested, never delivered.

Two different events, same shape of failure. A test was pinning down behaviour
the system did not have, which is worse than having no test: the green check
tells you the arc works, so nobody goes looking.

That is not a patch-level problem. As long as *submitting an event* and
*executing the resulting action* live in different places in the code, the
trap reproduces — someone adds a path, calls the old helper, and the machine
silently stops seeing reality. The fix was structural: a single entry point in
the service. Events go in, actions come out, and the action is executed right
there, immediately. Every place that used to reach into shutdown internals now
goes through that one door.

Round three then found two more bugs — in the fixes themselves. Guarding
against recursion, I had passed `nil` as the token, and the wait loop lost its
ability to schedule the next poll; and resetting the phase inside `stop()`
turned the backup timer into a no-op precisely when the daemon refused to die.
Both were mine, both were the cost of patching an edge instead of fixing the
model.

## What the reviewers cost and returned

Six rounds in total, across a cloud model and a local one. Cloud model: three
Critical, four Important, one false positive — it proposed `private(set)` on a
gate that is a `struct`, where mutating methods count as writes and the
compiler would have rejected it. The finding under that false fix was real
though, so it got closed a different way: the field went back to `private` and
four narrow wrappers went out instead, which also removed two methods from the
module's reach entirely.

The local 35B model is free and has no privacy boundary, so it runs every
round. Its useful range is narrow: on a 30K-character diff it reported nothing,
on a 5K one it verified the wrappers correctly. Earlier measurements said the
same — 2 true findings out of 10 on a 21K diff, but a hit on the same Critical
as the cloud model at 6.5K. Feed it per commit, not per branch, and treat
silence as no verdict at all.

Shipped in [v0.55.0](https://github.com/charoiteai/Charoite_audio/releases/tag/v0.55.0)
together with owner identification by capture channel — the feature that
replaced storing a voiceprint.
