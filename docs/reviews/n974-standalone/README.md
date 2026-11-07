# Standalone Linux pytest CI repair

Base: remote main `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be`.
The sole workflow change pins `jobs.tests.runs-on` to `ubuntu-24.04`.
The added guard requires that exact label, exactly one named pytest step and
the unchanged full command `python -m pytest tests/ -q -n 4 --dist loadgroup`.
No product/runtime, dependency, critical-zone or skip/filter policy changes.

This branch applies only the two-file patch of implementation
`8bc11fbcba40211a4fae84840469a1d23d955b4a`, not its history or other files.
Patch SHA256: `9e08f67f7f2cacf1e864a04902455f8b94beb4e535729fe1fabdf6310ee36a26`.
Both original base blobs equal current main: ci.yml `f6bbaf6d8788571fca03fe51ed9f2e3d81739522`,
test_workflows.py `695b05176c01bfc94682e75a338de652edd23790`.
Thus the source context of the copied two-file change is unchanged; standalone
acceptance still needs its own tests, independent OUTPUT and exact-snapshot CI.

This route intentionally excludes the unfinished retry adapter. It does not
accept its FD/UX boundary and will not merge its draft PR. Historical Linux
runner/native-library drift is evidence, not a one-variable cause or a claim
that Ubuntu26.04 is unsupported by the product. A24.04 label remains a moving
runner image; its actual full Linux run is required to establish this baseline.

Publication route: independent OUTPUT on this standalone snapshot, ordinary
push and draft PR to main, full Linux/macOS/lint/critical and all mandatory
checks on the actual PR checkout/tree, then normal expected-head acceptance.
No admin bypass or green claim from push-only mutation skips is allowed.

Standalone local acceptance: 60/60 addressed workflow/layout tests passed with
no skips/errors/failures; normal layout CLI passed and its generated map was
unchanged. Twelve original directed parsed-YAML mutations were rejected at
the actual focused guard's expected assertion; the good candidate passed before
and after. Exact base YAML was refused by the same new guard in a direct call.

The first standalone replay harness stopped on an unhandled original vector
operation after four runner cases; it is not counted complete. The corrected
explicit adapter for append/replace/duplicate completed all12. This was a
proof-driver correction; product sources remained unchanged. Fresh execution
is recorded at implementation62a8f42d. The final evidence-only commit is bound
by the two unchanged source hashes, not represented as another test run.
No Linux/macOS full CI, native audio, model or retry-FD result is inferred.
