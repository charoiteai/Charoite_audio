# Retry adapter scope

Base: 2106ed52c9b3f70f5d0c8b6363e9e3171ae767be. The existing nightly gate
already retries failed/interrupted/running status and suppresses only a finished
successful current night. Do not alter that policy.

A runtime-only wrapper independently uses a daily started marker before exec.
Its previously projected SHA256 is c88f5d6f10ca458e17f7d5e0ab94da70899b2ce0e3d3621b67f05e9c823a5a1c.
No incident-day marker was read; a historical lost retry is not established.

Deliver a public, generic process-lock exec adapter and synthetic tests. It opens
one persistent regular non-symlink lock file without truncation/unlink, takes
nonblocking POSIX flock through the existing file_locks primitive, marks its
descriptor inheritable, and execs the supplied argv unchanged. Contention exits
nonzero (75); invalid state/exec failure exits nonzero. Kernel lock lifetime
covers the command and only descendants that retain the descriptor. It does not
cover a descendant launched with close_fds after its last retaining ancestor
dies. There is no PID-file/stale-lock deletion or caller-controlled bypass flag.
The containing directory is trusted, owned by the caller and not writable by
other users; external unlink/rename/replacement of directory or lock is outside
this contract. Validate the opened regular file with fstat and O_NOFOLLOW.

Separately stage a private byte-hash-bound wrapper copy: remove only the two
started-marker suppression/creation blocks; export CHAROITE_SLOT=1 before the
existing nightly exec so its existing final-status gate is authoritative. Keep
all window, idle/resource, battery, UX and exec behavior. Stage an argv manifest
that wraps that staged wrapper with the process-lock adapter; the lock covers
the whole wrapper, including UX. Never modify the installed wrapper/launchd.
Never copy private source/paths into this public tree or review packet.

Before UX, the staged wrapper also asks that SAME existing night_gate CLI with
an explicit canonical data root/interpreter supplied by the private argv
manifest. This probe is advisory for UX only: brief/skip/defer omits UX; go or
an invalid/failed probe keeps the existing fail-open behavior. ALL outcomes then
reach the original nightly exec with SLOT=1. That source retains the final gate,
nightly_gate.json, cwd/env preparation and brief path; no direct brief dispatcher
or early return bypasses it. No gate policy is reimplemented. If the early and
final observations differ, the latter remains authoritative; resource/window
guards have already passed and are never bypassed.
Keep the original state-directory mkdir; remove only marker creation/cleanup.
The manifest binds the selected gate script and explicit data root, and
does not assert that the installed root or configuration is currently attested.

Execute a public synthetic shell wrapper before AND after this exact transform,
under the real exec-lock helper, with the same started marker, status, fake gate,
fake nightly/brief commands and ready artifact. Count UX and heavy/brief calls;
success must leave UX/heavy at zero. Preserve all resource/window refusals.

Before/after synthetic scenario: same fake command/status/work artifact, started
marker fails before and retries after; failed/interrupted/crash before final
status retry; failed with process rc=0 still retries; current success skips heavy
work but allows brief; next night retries; concurrent wrappers run one command;
exec failure releases the lock. Check ready artifact hashes and run counters.
Explicitly demonstrate both FD-retaining and close_fds descendant behavior after
owner death, without calling the latter protected. Signals are permitted only
to isolated processes created by the synthetic test, after verifying their full
command identity and with deterministic cleanup; never signal owner processes.
Synthetic time/status and fake commands only: no model, network, real graph,
status/config or installation changes. Hash-check the expected wrapper before
transformation and retain candidate/manifest hashes; mismatch stops staging.

This is a staged source candidate, not an installation-ready claim. Installed
acceptance is blocked until all actual UX/child execution boundaries have proved
FD retention, or an independent orphan-work exclusion gate covers non-retaining
branches. An intentionally inheriting fake child alone cannot discharge that
boundary. Preserve this limitation and leave task955 incomplete.

Use quiet_guard && heavy_run for execution. After code, independent Terra/Luna
OUTPUT review and targeted controls. Installed acceptance remains pending.
