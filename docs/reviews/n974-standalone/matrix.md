# Task974 finite acceptance matrix

These are18 defined checks, not18 completed tests. Historical CI observations, local workflow contracts, directed mutations and final remote execution are distinct.

|ID|Scenario/input|Expected observation / owner|
|---|---|---|
|M01|Frozen base jobs.tests runner|Existing ubuntu-latest observed; new explicit-runner guard fails before fix.|
|M02|Candidate jobs.tests runner|Exact ubuntu-24.04; guard passes.|
|M03|Pin reverted to latest|Directed workflow mutant rejected by the focused guard.|
|M04|Different Linux label or expression|Directed mutant rejected, not accepted by startswith/substring.|
|M05|Whole Linux pytest argv|Exactly full tests/ with existing -q/-n4/--dist loadgroup; no new selection/filtering; narrowed command mutant rejected.|
|M06|Native deps and Python|Python3.12, actual libportaudio2 install/retry/failure and pip command/pins preserved by exact diff plus existing guards.|
|M07|Job/step safety|Timeouts, failure propagation, credentials, permissions, triggers/concurrency unchanged; no new job/step conditions or continue-on-error.|
|M08|Other platform/jobs|Mac, lint, mutation/shards/verdict and nightly workflow bytes/labels unchanged; no blanket pin.|
|M09|Focused workflow suite|New guard plus existing tests/test_workflows.py and dependent contracts pass through quiet&&heavy.|
|M10|Product/source boundaries|audio.py, common conftest, pyproject/dependency locks and critical zones unchanged; docs/test-only remaining diff.|
|M11|Existing layout follow-up|Current remote main baseline; no inherited retry-adapter/layout changes. Normal layout guard passes without an exemption.|
|M12|Hook/privacy/snapshot|Normal hooks, Charoite AI identity, public/range privacy checks and clean source readback; exact final SHA recorded.|
|M13|Publication binding|Root-approved standalone ordinary draft PR to main; headRefOid equals accepted standalone final; record actual checkout SHA/tree and reviewed base delta.|
|M14|Full Linux CI|Actual24.04 runner and package versions recorded; full pytest finishes success, failures/import errors not hidden; inspect new skip/xfail.|
|M15|Full Mac CI|Existing platform/full suite succeeds on the same accepted snapshot; layout-only local pass is insufficient.|
|M16|Critical remote policy|All required PR shards and aggregate verdict pass, no unjudged/skipped/capped criticals; noncritical survivors explicit; push skip is not a pass.|
|M17|Other existing mandatory checks|Lint, public/security/docs/supply/title and all repository-required checks resolve green on accepted snapshot.|
|M18|Negative acceptance/scope|Any CI failure/incomplete/different unreviewed tree keeps974 open.24.04 success never claims26.04/native FD955 support.|

Local workflow-only guard tests establish policy, not audio behavior. The final Linux CI is the behavior check; there is no model or owner-runtime scenario in this matrix. Mutation implementations should minimally alter the actual parsed workflow input/contract, not simulate a guaranteed assertion failure unrelated to the guard.
