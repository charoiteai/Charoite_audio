# №947 — independent output/final-risk review

Reviewer: `gpt-6.1-terra`; static inspection only, no execution, model or API. Source base `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`; claimed before `f0a3ce57568efb5e2246fd25e627fa76114e152f`. Packet manifest SHA-256 `b7a73b576ca6e24e2d24cc0a0b6248efa2ffac1453b08903f8d84176cde44a0d`, independently checked. No other reviewer’s output or private/runtime data was read.

## Critical

None identified.

## Important

1. `diagnostics/causal-control.py:29-37,87-88`; `diagnostics/causal-binding.json` (`actual_http_generation_zero`); `diagnostics/synthetic-result.json` (`actual_http_requests`). The control sets socket hooks only after three project imports and prints `actual_http_requests: 0` and `real_generation_requests: 0` as literals, not measured counters. The hooks also do not cover every possible socket API (e.g. `connect_ex`); the pytest receipt has no independent network measurement in this packet. A future import or transport change could reach a real endpoint while the receipt still says zero, so the binding assertion is not independent evidence of zero HTTP. I found no evidence in the inspected execution path that this happened, and the fake adapter does intercept the intended generation path. Minimal fix: describe zero as a control-design assumption, not an observed count; for future execution put a fail-closed network boundary in place before project imports, covering child processes and all socket paths, and record actual boundary observations. Qualify the pytest zero claim unless independently supported. Do not rerun in this review.

2. `diagnostics/causal-control.py:87-94`; `diagnostics/REPORT.md:15,23`; `diagnostics/causal-binding.json`. `git rev-parse HEAD` identifies the checkout commit but does not establish a clean tracked tree, absence of untracked shadow modules, or bind all imported dependency bytes. The packet contains only the after snapshot and selected before file hashes, not the before files or an independently verifiable cleanliness receipt. Thus the observed before/after receipts are consistent with the report but this packet alone cannot establish the claimed clean exact-SHA execution or independently review the old implementation. Scenario: a modified imported module or an untracked shadow module preserves HEAD and the selected hashes while changing a result. Minimal fix: qualify the clean-snapshot and historical source claims as executor-reported, or supply a privacy-cleared immutable source archive or complete import closure with integrity and clean-tree evidence for each run; bind the receipt to that evidence. Existing before/after results need not be rerun merely to correct the wording.

## Minor

None identified.

The inspected current source supports the reported loopback and context guard and the full selected-source prompt behavior. The three synthetic cases and their stated limitations are internally consistent; the evidence supports the combined full-source/admission hypothesis at the fake-adapter boundary, not a guard-only causal attribution, real server acceptance or the cause of the installed incident. The 213-test and eight-check outcomes are executor receipts, not experiments repeated by this reviewer. Real incident cause remains undetermined; №947 must not be marked completed on this basis.
