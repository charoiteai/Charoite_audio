# Task818: final-risk findings and coordinating resolution

Reviewed source `0eeac71773eea05fc3bc6dc1cda7583334b18e18`; original comparison
base `bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`. Both independent reviewers read
the same clean public snapshot (950files, manifest
`144d8efddc66c444bd07e5b6d2619a86153e897c161204bf877113bcd6fadcc3`), without Git
history or owner data. gpt-6.1-terra/high: C0/I1/M0;
gpt-6-luna/high: C0/I0/M0. This was code reading, not reviewer execution.

## Accepted Important: the full-rebuild promise was too broad

Terra correctly identified that the prior P01 wording could promise retention
of every previous generated warning/link, although full rebuild refreshes the
body and source inventory. The coordinator executed real public nightly
rebuild functions twice with identical synthetic inputs on both pinned trees.
A valid changed generation double omitted the old body warning/link and old
extra source link. Both baseline and candidate replaced those old generated
elements, kept the new warning/link and current-member source links, and were
stable after the first rebuild. Candidate retained exact author indentation;
baseline did not. With a revision field, both trees preserved the whole original
file and invoked the generation double zero times. These controls use no real
model and no stored owner files. Raw observations are
`reports/handoff/tasks/818_retention_before.json` and
`reports/handoff/tasks/818_retention_after.json`.

The coordinator keeps the established full-rebuild ownership: author payload is
retained, generated body/current source inventory refreshed, revision hold
preserves the whole original. Blindly merging arbitrary old generated links or
warning lines could retain deleted sources/stale conclusions and is a separate
policy decision. The contracts now state this boundary explicitly; P01 includes
the changed-response and revision-hold controls. Stronger old-generated-text
retention is **unresolved follow-up891**, not a completed feature or a waiver of
an applicable parser defect. The original real112-file migration remains open
and unavailable. This documentation resolution does not certify arbitrary
model content or claim all prior warning text survives every full rebuild.

## Evidence and repeat

The original 0ee full local suite passed10422/8skip/7xfail in178.92s; this is
coordinating execution. New commits still require applicable exact-head checks.
Previous90b canonical mutation evidence remains bound to90b, never relabeled.

Export the two pinned public Git trees into separate empty temporary directories
using ordinary Git archive. Under fresh quiet/shared heavy resource gates and
an isolated data root, run the following for each exported tree:

```sh
.venv/bin/python -B tests/dossier_retention_control.py "$task_public_snapshot" "$task_source_sha"
```

The probe owns temporary graphs/data and uses a fixed generation double; its
source is retained with the reports. It executes two ordinary full rebuilds and
two revision-held attempts per tree. Its old-source author-indentation failure
is observed rather than relabeled as success. This finite control is not a real
migration or a guarantee of every historical layout, producer or annotation.
