---
layout: post
title: "A crash in a background thread should fail the run"
date: 2026-08-25 17:45:00 +0300
---

This devlog keeps returning to false green; apparently that is the theme.

Charoite's daemon is thread-heavy: capture pumps, STT loops, watchdogs. In
tests, those threads die the way threads do — an exception nobody joins on,
a traceback printed to a log nobody reads, and pytest reports the run green,
because from the main thread's point of view nothing went wrong. The worst
version of this: an assertion *inside* a background thread fails, which is a
crash of the thread and a pass of the test.

This week the suite got a gate for it. A `threading.excepthook` installed for
every test records background-thread exceptions, and the run fails if any
were seen. Sixty lines of test code, eleven lines of `pyproject.toml`, plus
a canary test that deliberately crashes a thread and asserts the gate catches
it.

Two details survived review and are worth writing down.

First, the honest boundary. The reviewer's finding was not a bug but an
overclaim: the gate's comment promised it catches background crashes,
period. It doesn't — it catches a crash that happens *inside its test's
window*. A thread that leaks past the end of its test attributes the
exception to a report that is already closed, and the run stays green. Late
crashes are caught by something else entirely: join discipline, every test
joining what it spawns. The comment now says exactly that. A guard that
states its limits is a guard; a guard that overstates them is the false
green it was built against, one level up.

Second, the canary is *deliberately* fragile. It asserts the gate is wired
in `pyproject.toml` specifically, so moving the gate breaks the canary
loudly instead of leaving a silently disarmed suite. Coupling a test to a
config file's location is normally a smell — here it is the point.

The gate found its first real victim immediately: a dead-channel test whose
cleanup raised `IndexError` in a `finally` block, masking the actual
failure underneath for who knows how long. Joined the pump threads before
asserting, and the real failure surfaced. Green that lies is worse than
red: red gets investigated.
