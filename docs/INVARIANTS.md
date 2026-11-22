# Live Pipeline Invariants

***English** · [Русский](ru/INVARIANTS.md) · [中文](zh/INVARIANTS.md)*

These invariants are stated **before** tests and reviewed independently against the specification rather than the implementation. An invariant is not a description of code; it is a promise whose violation is an incident even if every existing test is green.

## I-1. Audio buffer integrity

No sample accepted by `AudioHub._append` disappears silently.

For every channel at any moment:

    accepted = pulled + remaining_in_buffer + reported_lost

with two explicit contract clauses:
- overlap (`overlap_s`) is delivered twice at the boundary of adjacent chunks;
- `stop()` may omit a trailing tail shorter than the minimum chunk, but must keep it in the on-disk recording.

Testable consequences:
- a channel buffer never exceeds `BUF_CAP_S`;
- every buffer trim increments the loss counter by the exact trimmed amount — neither silent drops nor phantom counts are allowed;
- reported losses reach the user: the cumulative report meets the reporting threshold and is flushed on `stop()`.

## I-2. Honest degradation without a device

Failure of any source (capture channel or recording sink) neither kills the pipeline nor stays silent.

- Surviving channels continue to be served: failure of one source never stops `_pump` or corrupts neighbouring channels.
- Degradation is observable externally within 30 seconds via status events and/or `health_snapshot` (`recording_ok`, rising channel age).
- The age of a dead channel grows monotonically until real recovery; housekeeping actions (restart attempts, anti-storm backoff, status reports) never reset its age.
- The converse is also an invariant: a channel delivering frames continuously is never declared dead, and degradation does not trip on a single slow chunk (hysteresis).

## Verification against code

- **I-1** — `AudioHub._append` in `src/audio.py`: the `BUF_CAP_S` (60 s) cap trims only the excess, returns the dropped count and accumulates it via `_note_drop`; loss notices emit at most once per 30 s once noticeable, and the tail is flushed on `stop()` (`_say_last_drops`). Covered by `tests/test_audio_buffer_drop.py`.
- **I-2** — `_pump` survives a single-channel or watchdog failure (`tests/test_audio_capture.py`); `health_snapshot()` exposes `recording_ok`, per-channel age, `pump_alive` and `pump_failures`; failed restarts never reset age and retry at most once per 30 s; diarization shedding hysteresis is covered by `tests/test_stt_runtime.py`.
- **Property-based coverage:** `hypothesis` is not a dependency yet; the properties above are enforced by example-based tests.

## Change policy

A new invariant enters this file only after independent review of the wording itself (checking for blind spots in the contract before code). Any edit to an invariant wording requires a new review.
