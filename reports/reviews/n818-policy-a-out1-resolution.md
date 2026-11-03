# Task 818: OUTPUT1 resolution

Reviewed source b9d37c8219de16d9c4376b963cfdd9b150709145; comparison base
bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81. Root read both complete independent
reports: gpt-6.1-terra/high C2/I0/M0 and gpt-6-luna/high C0/I1/M0, identical
packet ba5c578deefd10c902cacfaa311eaf8d4e7b3c88794a79465970ac969c999bca.
Their findings are static review, not executed experiments.

Root reproduced the admission predicates and real forget plans on constructed
data using `reports/handoff/tasks/818_out1_admission_probe.py`. Fresh quiet and
shared heavy gates passed. No model, external network or actual apply occurred.
The exact before result is `818_out1_admission_before.json` in that task folder.
The first probe used a lexical temporary path that differed from its resolved
graph address: membership checks were invalid. Resolving the synthetic root
corrected the probe; only the corrected result is used below.

| Finding | Root decision | Evidence and required check |
|---|---|---|
| Terra C1: revision comment with invalid unrelated YAML | Accepted | Complete=True, scalar=`2026-11-03 # checked`, revision empty instead of date. Fix scalar interpretation; actual ordinary/full rebuild must hold before generation/backup, preserve bytes and process another theme. |
| Terra C2: closed fence changes delete permission | Accepted | Same source file: old keep reason nonempty, new empty; old real plan delete=False/edit=True, new delete=True/edit=False. Restore conservative deletion veto without a second ordinary section parser. Verify plan/apply plus an unrelated eligible dossier. |
| Luna I1: forget clears damaged revision | Rejected as a new regression or required hold | On identical complete synthetic file with U+FFFD in the revision, both base and candidate real plans delete=False/edit=True. Both proposed texts are equal and clear the revision. `without_revision` explicitly documents this source-forgetting behavior. PR900 protects rebuild even under full; it does not cancel explicit forget of a cited source. Contract wording clarified, without expanding deletion or overriding ambiguous-file policy A. |

Executor source53b963148b4e1ef08ff2a60873617d6d4fde66b1 addresses both Critical
findings. Executor BEFORE20FAIL14PASS, focusedAFTER34PASS, finite952PASS3XFAIL
(14.00s), deterministic generation only. Root repeated the same scalar and real
plan probe: revision now equals the date, new deletion is refused and the existing
edit route matches the base. The final replay also loads the historical base
frontmatter dependency explicitly for the component comparison. The earlier
malformed lexical-path probe is retained as invalid evidence, not accepted proof.

Both Critical findings remain open until independent review of the updated
identical snapshot by both selected reviewers. No Critical
has been downgraded. New routine repair stays in existing task 818.

Both reviewers identify whole-directory quarantine/displaced purges and
same-second backup naming as unchanged pre-existing limits. Pending tasks 874
and 875 preserve those scenarios; they do not certify safety or waive a finding.
Bounded parser acceptance cannot claim universal protection of every descendant
or every historical preimage. CI, mutations, final risk and delivery remain open.
