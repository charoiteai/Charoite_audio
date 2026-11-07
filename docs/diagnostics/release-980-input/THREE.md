# 980: three questions — INPUT ONLY
1. Tried: root reports frozen vanilla external failed with `GateError: release PR expected push skips`; this worker did not rerun it.
2. Source: frozen `scripts/release970_gate.py:437-455` allows exactly one global skip pair; `ci.yml:221,334` conditionally skips one pair per push run.
3. Request: smallest reviewed external PR-metadata repair preserving the historical frozen proof; no waiver, rerun, delivery, or owner operations.
4. Shortest path: independently pinned supplemental entry point loads the unchanged frozen gate and replaces ONLY its PR validator in memory.
5. Preserve: original main, ROOT, approval four fields/hash, runtime preflight, 32-shard artifacts/import validation, canonical merge, and receipt comparison.
6. Benefit criterion: the same complete authenticated multi-push input is accepted; every bad/incomplete/unpaired/unknown-skip control remains rejected.
7. Evidence: raw PR rollup is 28 (24 SUCCESS, 4 SKIPPED); raw REST is 29 (25 success, 4 skipped), with one successful REST-only Copilot check.
8. Scope: this commit contains documents and public raw fixtures only. All BEFORE/AFTER execution controls are PLANNED, NOT RUN.
9. Gate: both independent INPUT reviews and root code GO precede any implementation; both OUTPUT reviews precede corrected external execution.
