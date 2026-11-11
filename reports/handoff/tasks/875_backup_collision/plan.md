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
