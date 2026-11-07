# INPUT review — task 955

Reviewed only `/tmp/n955-input-neutral-20261106` (manifest SHA256 `07604bc6d8c74f94c5a48beee3181d3d21f2b889c796dc3a7dc0f01b1d3c1226`; base `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be`). The manifest hash matches. No implementation, installation, model, network, or tests were run.

## Critical

None.

## Important

None. The bounded proposal is internally consistent: retain `night_gate` policy; remove only the private wrapper's started-marker suppression/creation; export `CHAROITE_SLOT=1` so the existing final-status gate remains authoritative; and stage an argv manifest that puts a nonblocking kernel lock around the entire wrapper/UX. The lock-file requirements exclude PID-based stale cleanup and unlocked fallback. The specified synthetic cases cover rc-zero failed status via status-based retry semantics, concurrency, parent death with a live inherited-lock descendant, and exec failure. Installed acceptance is explicitly pending.

## Minor

None.

## Acceptance conditions for OUTPUT review

Confirm the staged lock open is race-safe against symlinks (for example, no-follow open plus `fstat` regular-file validation), never truncates or unlinks the persistent inode, and fails closed on setup/acquisition errors. Confirm the lock descriptor survives `exec` and is inherited by spawned descendants, the argv manifest actually places the lock outside the whole staged wrapper, only the two marker blocks are removed, and the installed wrapper/launchd remain untouched. These are implementation checks, not findings against this plan.
