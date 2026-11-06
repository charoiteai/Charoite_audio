# INPUT review — nightly diagnostic 947

Reviewer: GPT-6 Luna (independent static review)
Packet: `/tmp/charoite947-input-packet`
Manifest SHA256: `72b6a5817c38182aa4a7dd53a856eb84dfc2618806503d771ff2540a275f6683`
Base: `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`
Review type: постановка / статическое ревью; runtime и эксперимент не запускались.

## Important

- `input-plan.md:16` — Privacy boundary for installed launchd/wrapper inspection is underspecified. The plan allows comparison of installed script, interpreter and wrapper, and says to project allowlisted launchd values, but it does not name the permitted files/keys or define how to avoid emitting paths and unrelated environment values while proving the root/env chain. Scenario: an investigator follows a wrapper into a plist or environment file and captures a credential-bearing or owner-specific field in notes/output. Minimal fix: before inspection, enumerate exact files and keys to read/hash; hash opaque files where contents are unnecessary; permit only explicit enum/boolean/numeric fields, keep paths and other values in-memory, and record only allowlisted comparisons. This makes the stated data boundary auditable.

## Minor

- `input-plan.md:18-20,24` — The acceptance matrix does not explicitly exercise a scan failure co-occurring with refusals at the aggregate threshold, although the documented exit contract returns 4 when both conditions occur. Future telemetry also lists generic IO failures without naming scan/index failure separately. Scenario: tests prove the threshold exit for refusals alone and scan failure alone, but miss a regression that drops one cause or misclassifies the combined exit; the incident evidence remains ambiguous. Minimal fix: add one deterministic double case with `scan_failed` plus refusal count at threshold and assert exit 4 and both reason counters; name scan/index failure counters separately if they are intended to diagnose this path.

## Assessment

No Critical finding. The plan appropriately separates current from incident-time facts and explicitly says synthetic success cannot prove owner nightly success. Findings are gaps in operational privacy detail and one combined-cause acceptance case, not evidence of a product regression.
