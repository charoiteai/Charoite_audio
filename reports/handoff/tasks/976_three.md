# 976: finite claim-search progress regression

- Source: release-970 actual run 42388856807, attempt 1, K14 canonical log: key `src/dossier_incremental.py::_check_claims::Add → Sub::b9a810e9dcc6a998#1` reached outer 480 s cap; whole run failed.
- Request: finish the release through a meaningful finite regression, without treating a cap as a kill or changing production/mutation policy.
- Base: `50b0704fe280360b1d7f828fd601c6216959e55d`.
- Shortest path: assert forward progress at the checker string-search boundary in test fixtures; retain actual checker execution and all existing selected tests.
- Benefit proof: baseline/refusals unchanged; exact legacy-generated mutant fails an ordinary assertion across the whole canonical selected suite without `-x` or an outer timeout.
- Isolation: synthetic strings, owned child and pipe, temporary source copy; no model, graph, installed app, source overlay or production edits.
