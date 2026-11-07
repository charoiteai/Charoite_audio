# 976 journal

- 2026-11-07: created isolated candidate from exact 50b; read existing checker, mutation scan/applied, shared pytest and child cleanup mechanisms. Recorded finite disposition before changing tests. Actual 16-shard release proof remains FAIL; full new verification has not run.
- Focused: initial simple target 17 PASS; expanded negative harness 23 PASS/1 FAIL (incorrect empty-tmp assumption), corrected to before/after inventory; final target 27 PASS/4.87 s.
- Canonical selector: honest 16→17 extension, retained; an obsolete count assertion stopped first whole harness before execution.
- First whole evidence: baseline16 PASS15.16 elapsed, baseline17 PASS19.96, exactb9 ordinary rc1/193FAIL+431PASS22.92 elapsed. Final diagnostic assertion failed because canonical tail truncates the error message; preserved FIRST-WHOLE-RECEIPT.json.
- Corrected passive output receipt: original16 604PASS14.65s, new17 624PASS19.90s, exactb9 193FAIL+431PASS22.54s,192progressAssertionError,0pytesttimeout. Owned canonical clone removed by TemporaryDirectory, worktree source unchanged.
- Layout guard found diagnostic Python misplaced under docs; moved replay helper into tests, regenerated only generated layout map. No classification exception or source-policy change.
- Full layout/import-boundary target:84PASS/68.56s. Before publication readback: release PR906 still50b/base005, candidate branch absent remotely.
