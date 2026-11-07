# 975 — bounded shallow-CI test repair (INPUT)

1. Source: ordinary CI run42388856753, definitionbbe5a6487cb016d3ef4529944b9e686a426354ca.
2. Linux job128451970644:5 failed/11128 passed; macOS job128451970411:5 failed/11140 passed.
3. Both primary logs show depth1 checkout, missing historical79ea validator, three checkout50b failures, one CLI umask failure.
4. Dedicated full-history plan+policy job128451970380:27 passed; that is separate existing evidence.
5. Parent scope: repair only tests/test_release970_observer.py and tests/test_private_permissions.py, plus reports.
6. Desired result: ordinary shallow CI executes real positive/negative observer fixtures and preserves the permissions guard.
7. Existing publisher WIP is a proposal already written before this INPUT;16+/13−, tests NOT RUN.
8. WIP patch SHA256:6d924fa56ee7c3f54be676c8156566ad7cdab9453d9415c76fbdba68c2d21a04.
9. Shortest route: use the available current HEAD/tree for a synthetic mini-plan; retain real two-child pytest execution.
10. Keep the historical79ea before/after experience as its explicit receipt, rather than a permanent history requirement.
11. Classify only scripts/release970_gate.py through the existing exact UMASK_FORGIVEN mechanism after code-based review.
12. No skip, assertion deletion, broad exception, canonical import change, helper/observer/workflow/plan/runtime edits.
13. Native local tests require a fresh quiet_guard0 and the existing heavy_run chain; previous guard3 is not permission.
14. After both INPUT reviews and rootGO: targeted full/shallow checks, ordinary Linux+macOS CI on exact repair SHA, OUTPUT/privacy/hooks.
15. Keep run42388856807/attempt1 onbbe and runtime50b separate; no new682 run or evidence-SHA relabeling.
