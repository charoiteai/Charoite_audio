# 0.103.0 devlog draft — task 970

Status: draft, publication pending. This report is outside the devlog site.
The post has `published: false`; do not remove it before actual release
acceptance. No release, signed manifest, updater availability or installation
has been verified by this documentation task.

Base: `005832892bfb8b9d8cca9d90ed70537d69242d3a`.
Branch: `docs/970-release-0103-devlog`.
Post: [add to a dossier without losing its history](../../../devlog/_posts/2026-11-08-add-to-a-dossier-without-losing-its-history.md).

The post follows `devlog/README.md` and the bilingual 0.102.0 post.
Its date is 2026-11-08 02:00:00 +0300 (Europe/Moscow), already elapsed when
drafting began: the observed clock was 2026-11-07 23:20:59 UTC.
The date in the release-please changelog is not treated as delivery evidence.

## Tracked sources checked

The 0.103.0 changelog was read from release PR906 head
`94867ecb54593018b0568a18fa9501770cdfb768:CHANGELOG.md`.
The following commits were verified as ancestors of the pinned base:

| Change | Exact commit | Public tracked evidence |
| --- | --- | --- |
| Incremental dossier history, attributed corrections, retryable failure and unchanged-membership behavior | `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962` (PR909) | `docs/FEATURES.md`; `docs/design/incremental-dossier-acceptance.md` |
| Shared parsing, exact author payload and conservative ambiguous-boundary refusal | `f0a3ce57568efb5e2246fd25e627fa76114e152f` (PR902) | `docs/design/dossier-parser-acceptance.md`; commit diff and message |
| Reviewed archive pin, app public-key anchor and bundle version | `ca7144d9b46481c9572fa74f03a112b4207b7639` (PR908) | `docs/RELEASING.md`; `scripts/sign_release_manifest.py` |
| Closed, content-free phase/reason counts and distinct adapter entries | `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be` (PR910) | `src/dossier_telemetry.py`; `scripts/nightly_dossier.py`; `docs/ARCHITECTURE.md` |
| Explicit full Linux pytest baseline; unchanged full command | `005832892bfb8b9d8cca9d90ed70537d69242d3a` (PR912) | `.github/workflows/ci.yml`; `docs/reviews/n974-standalone/README.md` |

These are source checks, not reruns of the implementation's experiments.
The draft makes no new performance, full mutation, real-data migration,
historical incident diagnosis or installed-runtime claim.

## Before publication

1. Complete the parent task's actual release and installed-runtime acceptance
   against its recorded immutable tag, archive and manifest evidence.
2. Check that the final 0.103.0 changelog still matches the five changes above.
3. Replace “changes prepared for” / “изменения, подготовленные для” with final
   release wording; add the verified release link and only delivery claims
   supported by the parent task's evidence.
4. Remove `published: false` and keep a date/time already in the past in
   Europe/Moscow when publishing. Do not use the draft's timestamp as proof
   of when the release was delivered.
5. Run the normal devlog CI checks on that final publication snapshot.

Only public tracked content and the repository's `AGENTS.md` were read.
No owner data, credentials, keys, application, models or tests
were accessed or run. This is a local documentation commit; no push, PR,
merge, release mutation or publication is part of this task.
