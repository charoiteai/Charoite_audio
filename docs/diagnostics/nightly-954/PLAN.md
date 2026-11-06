# Nightly refusal telemetry — bounded contract

Source: issue 954 and `docs/diagnostics/nightly-947/REPORT.md`; prior cause remains unknown.
The existing generic model-silent label conflates admission, transport and validation refusals.
Add an in-memory run collector and one aggregate JSON line; no paths, graph/topic names, prompts,
response bodies, exception strings, model identifiers or arbitrary keys enter this projection.
Count visited build topics, legacy threshold attempts, local-completion attempts, adapter stream entries,
request outcomes by closed phase/reason enums, committed topics and final topic refusals separately.
A stream entry proves an adapter invocation, never a physical HTTP request or successful response.
Instrument actual complete-input admission, model contract, stream, output validation and publication boundaries.
Classify only typed exceptions and explicit branches; unexpected exceptions use unknown/error.
A malformed first answer followed by a valid retry retains request history but adds no final refusal.
Keep request telemetry separate from terminal topic accounting; append/judges can make several requests.
Preserve existing exceptions, two-attempt limits, queue ordering, context/window controls, locks, snapshots,
author-text protection, resource gates, refusal threshold max(3, attempts//10), scan-failed exit 4.
Emit the closed aggregate even on run interruption from the existing finally path; do not add durable state.
Use safe wording for shell code 2/4; do not claim the model was silent when the cause is unknown.
Acceptance: synthetic admission-before-stream, contract refusal, timeout, empty, malformed/recovered,
append audit refusal, final IO refusal, success, interruption/context cleanup and no-content sentinel.
Regression checks: existing dossier incremental/nightly tests, threshold and combined scan-failure exits.
Only synthetic fixtures/doubles; real nightly, owner runtime and real model calls are out of scope.
Rollback: remove additive telemetry and wording changes; no persisted schema or migration exists.

## Accepted INPUT refinements

Both independent reviews used the same base/archive and original plan. All findings accepted.
The Python surface also says build refusals, including preflight errors; shell codes 2/4 remain failed.
The collector counts each completed legacy attempt before `_drive` can be interrupted. Its outer finally
emits after the existing index-publication finally even if that publication raises; exceptions keep propagating.
A topic started is one actual `_build` entry (not dry/skipped/queued work). A completed topic is one return.
Exactly one closed final reason is counted per legacy refusal; a successful retry has none.
A request attempt starts at `local_complete`; exactly one phase/reason outcome records its return/exception.
Stream entries count only the adapter call boundary. Returned means text returned, not validated or committed.
Phases: preflight, admission, contract, stream, fallback.
Reasons: returned, empty_reply, preflight_refused, context_budget, contract_refused, timeout, http_error,
stream_error, transport_error, unexpected_error, interrupted, input_refused, format_refused, validation_refused,
io_refused, unknown. Final refusal uses the last failed request unless a later explicit topic gate failed.
The aggregate schema contains only fixed keys, booleans and integer counts, never exception attributes/text.
Acceptance includes exact multi-request append counts, no-source-contribution success, an interrupted second
topic after a completed first topic, and index-publication exceptions. Shell code 4 keeps both failure labels.

Scan-time unreadable refusals are included in final_refusals/input_refused and separately in scan_refusals;
they do not invent build entries, legacy attempts or local completion attempts. topics_scanned counts
all scanned clusters; topics_started only counts `_build` entries. Sum(final_reasons) = final_refusals
= scan_refusals + completed-build refusals. A scan failure is a separate graph count, not a topic refusal.

OUTPUT refinement: typed LLMHTTPError with status 200 is stream_error, never inferred from detail/body.
If aggregate stdout fails during an existing run exception, retain the original exception; aggregate delivery
is then unconfirmed. Without an original exception, the output error remains visible and propagates.

Mutation follow-up: a new generation/candidate attempt clears only its pending failure attribution;
request history counters remain accumulated. A current request's failure is explicit optional Reason,
not an independent boolean. Explicit gate refusal and outer request-aware refusal use separate functions.
JSON object key order is not a wire promise; only version, closed field names, types and values are.
The CLI is explicitly imported by a real full-path test, so mutation suite discovery includes the telemetry
scenarios for nightly_dossier as well as local_complete. No mutation waiver or critical-zone reduction.

Final-risk refinement: every index publication failure is marked at the shared write_index boundary,
including a publication initiated by the last topic's g.done(). Count one failed graph once, without a retry.
Cleanup cannot replace the run's own active exception; a caller's previously handled exception is not
an active run exception. Emitter BaseException cannot replace an original run error, but propagates normally
when there is no original error. Aggregate delivery during an emitter failure remains unconfirmed.
