# Backup collision reproduction

Base: 910b923c85b6a2398a6a7426b4f0feb2aba1f211.
Scope: synthetic reproduction of two distinct preimages sharing a timestamp,
byte-for-byte restoration checks. No owner data, model calls, service changes,
publication or production fix before the baseline experiment.

Each case must create a fresh temporary root. Source, backups and restore
paths must remain inside it. No reusable graph path or symlink is accepted.
Set CHAROITE_ROOT to the synthetic root before product imports.

1. Write A to one source path, then replace it with B before the second call.
   Assert the initial backup equals A and record path equality. Call the production dossier.backup twice, same folder/name/stamp, distinct
   UTF-8 bytes A and B. Capture SHA256 immediately and after the second call.
2. Copy each returned backup path to separate restoration destinations after
   both calls. Compare actual bytes against A and B; report retention failure.
3. Control: distinct timestamps must retain and restore both preimages.
4. Review wrapper: repeat through nightly_dossier_review._backup (returns None;
   observe expected destination). Distinguish wrapper from full model pipeline.
5. Follow-up only after baseline: choose exclusive destination creation with
   safe refusal, or unique version names; check rebuild/review, concurrency,
   copy failure, CAS refusal, rollback and retention before accepting a fix.

Local gate returned 3: updating_graph and resident models above memory limit.
Do not run even narrow tests until fresh quiet_guard + heavy_run allow them.
Do not infer successful experiment from source inspection.

## Baseline execution

Executed on Darwin/Python3.12.13 at tested commit c178149a. Host gate allowed
execution. Result: collision_reproduced (exit1); see baseline-result.json.
Both helper and review wrapper replace A with B for identical timestamps:
A cannot be restored after the second backup; B can. Different timestamps
restore both A and B byte-for-byte. Source B remains unchanged in all cases.
No model calls, full pipeline run or production fix is claimed. The earlier
busy gate describes the initial attempt, not this completed baseline.

## Fix contract (after reproduced baseline)

Base4e17fb327f4be9ccf4f4f46086d21abf34e7c5dd; runtime base910b923c.
An occupied backup name must never be overwritten, even for identical bytes.
Select fail-closed FileExistsError; existing rebuild/review handlers must leave
current dossier and index intact. No destination format or retention-policy change.
Stage a complete copy with metadata in a uniquely named temporary file within
its backup directory, then publish with atomic os.link(temp,destination), which
fails if any destination entry already exists. Close/remove the temporary name
on success or failure. Unsupported hardlinks fail closed through OSError.
No os.replace/overwrite and no check-then-write race. Production graph locks,
source snapshots and CAS writer are unchanged. Cross-process competitors for
one destination, with no concurrent retention pruning, may yield exactly one winner; backup must equal that winner's
whole preimage. The normal source/root trust boundary remains unchanged.

Acceptance matrix:
1. Original byte-exact four-case probe: same timestamp refuses second backup,
   A still restores, currentB remains; distinct stamps restore both.
2. Same content retry and occupied symlink/directory: reject without alteration.
3. Concurrent callers: one successful complete copy and one FileExistsError;
   no partial final destination or leftover temporary name.
4. Copy/link/metadata failure: no source modification, no published partial copy,
   preserved prior destination, temporary cleanup; missing source remains None.
5. Real synthetic rebuild and review callers: collision prevents final write;
   first backup and currentB/index persist; no real model/owner-data access.
6. Existing author-race/CAS refusal, failed writer, restore and retention tests;
   successful copy preserves metadata and multiple filenames share a stamp.
7. Targeted regression suite; fault mutations reinstate copy2 overwrite, skip
   publication or swallow collision and must be killed. Project changed-line
   critical mutation policy plus final required test/CI gates remain mandatory.
8. Independent INPUT and OUTPUT Terra6.1/Luna6/Sol6.1 on same bounded snapshot;
   normal commits/hooks; no publish/merge without authorized delivery scope.

INPUT resolution: all three reviewers report no Critical; Luna Important is
covered by real caller collision tests asserting no dossier writer, current
bytes/index preserved, another theme continuing, and existing CAS tests with
stronger exact-byte backup assertions. Review reports must still be written on
refusal: only the dossier final write is forbidden, not the diagnostic report.
