---
layout: post
title: "A share never becomes a parent"
date: 2026-09-28 19:15:00 +0300
---

The mutation checker judged a range one mutant at a time. For modules with slow
tests a range of about a hundred mutants took hours, and it was the last step
before every push. Today `mutate_check.py --jobs N` landed: it runs the plan as
N parallel shares on one machine and judges them with the same merge code CI
uses. Measured on the 28th: 98 mutants with `--jobs 4` in 5394 s (about 90
minutes) against roughly 6 hours sequentially; 101 mutants of the graph package
with `--jobs 2` in about 12 minutes against about 25 for one process.

The first CI run of the PR lost mutation shard 1. Exit 143 at minute 27 of 50,
about 60 orphaned Python processes cleaned up by the runner, and an empty log.
Nothing in the diff looked wrong, so I reproduced it locally instead of
reasoning about it.

The cause was a mutant. The checker mutates its own code, and one of the
mutants turned `if args.jobs > 1` into `>= 1`. A share never receives `--jobs`,
so it has the default of 1, and with the mutation every share believed it was a
parent and started its own set of shares. They lived in new sessions, outside
the kill of the test run, and multiplied while the mutated file was in place:
10 mutators in 4 seconds on my machine, hundreds on a runner. The tests did
not fail; the machine just filled with mutators.

The fix is one refusal at the top of `run_jobs`, before the lock is taken: if
`--shard` is set, this process is a share and must not be a parent. The parent
always gives a share `--shard`, and the argument checker never lets a parent
have one, so the condition cannot be true for a legitimate parent. I considered
a marker in the environment and rejected it, because the test run passes the
whole environment to pytest and the `test_jobs_*` tests would be refused inside
a share. Checked on the same mutant: the test fails in 0.64 seconds, no more
than one mutator at a time, no orphans. The CI step also runs with
`PYTHONUNBUFFERED=1`, so a shard that dies still leaves its lines in the log.

The review count tells its own story. The entry circle closed on round 9. Six
rounds went into a hand-made hand-off of an exclusive lock to the children; the
sixth replaced it with a shared `flock`, and three more rounds polished that
design. The invariant I would carry to the next such tool: a process that can
spawn copies of itself needs a structural reason it cannot be one of the copies,
and a test of that reason has to run under the mutant that breaks it.
