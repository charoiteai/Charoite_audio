# 0.103.0 devlog handoff — task 970

Status: final release wording; delivery through a documentation PR. This report is outside
the devlog site. The post describes the published release and contains no
claim about a completed installation or a running installed UI.

Original draft base: `005832892bfb8b9d8cca9d90ed70537d69242d3a`.
Final branch base: `13efa6c584f70b10493f14f5cd06940eb29cc871` (release PR906).
The local `v0.103.0` tag resolves to that exact commit and tree
`41698fc26e94f4431b5a9f1bb89aef15488b378f`.
Branch: `docs/970-release-0103-devlog`.
Post: [add to a dossier without losing its history](../../../devlog/_posts/2026-11-08-add-to-a-dossier-without-losing-its-history.md).

The post follows `devlog/README.md` and the bilingual 0.102.0 post.
Its final date is 2026-11-08 02:50:00 +0300 (Europe/Moscow), already elapsed
at the observed clock 2026-11-07 23:54:12 UTC and after the release handoff's
confirmed promotion. `published: false` is removed. The changelog's earlier
date is not treated as delivery evidence.

## Tracked sources checked

The 0.103.0 changelog was read from release PR906 head
`94867ecb54593018b0568a18fa9501770cdfb768:CHANGELOG.md`.
Its five release entries were rechecked at the final release commit above.
The following commits were verified as ancestors of the pinned base:

| Change | Exact commit | Public tracked evidence |
| --- | --- | --- |
| Incremental dossier history, attributed corrections, retryable failure and unchanged-membership behavior | `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962` (PR909) | `docs/FEATURES.md`; `docs/design/incremental-dossier-acceptance.md` |
| Shared parsing, exact author payload and conservative ambiguous-boundary refusal | `f0a3ce57568efb5e2246fd25e627fa76114e152f` (PR902) | `docs/design/dossier-parser-acceptance.md`; commit diff and message |
| Reviewed archive pin, app public-key anchor and bundle version | `ca7144d9b46481c9572fa74f03a112b4207b7639` (PR908) | `docs/RELEASING.md`; `scripts/sign_release_manifest.py` |
| Closed, content-free phase/reason counts and distinct adapter entries | `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be` (PR910) | `src/dossier_telemetry.py`; `scripts/nightly_dossier.py`; `docs/ARCHITECTURE.md` |
| Explicit full Linux pytest baseline; unchanged full command | `005832892bfb8b9d8cca9d90ed70537d69242d3a` (PR912) | `.github/workflows/ci.yml`; `docs/reviews/n974-standalone/README.md` |

These are source checks, not reruns of the implementation's experiments.
The post makes no new performance, full mutation, real-data migration,
historical incident diagnosis or installed-runtime claim.

## Public release verified

The GitHub connector independently read the [tagged release](https://api.github.com/repos/charoiteai/Charoite_audio/releases/tags/v0.103.0)
and [latest release](https://api.github.com/repos/charoiteai/Charoite_audio/releases/latest)
on 2026-11-08 (Europe/Moscow). Both identify release `437910696`, tag
`v0.103.0`, target `13efa6c584f70b10493f14f5cd06940eb29cc871`,
`draft: false`, `prerelease: false`, and the same six uploaded assets.
The public [release page](https://github.com/charoiteai/Charoite_audio/releases/tag/v0.103.0)
is linked in the post.

The uploaded ZIP metadata has asset ID `722287165`, size `150607491` bytes
and SHA-256 `16600db97d71611122b1725ab91e8df54ecd759e71410c179b7661c8ae0241b1`.
The DMG metadata has SHA-256
`7bfe6c87bea961f82a4c733111ffe99a1b87be9d79d977d5ed38c23d19c899f3`.
Both manifest assets are present. Metadata inspection does not itself repeat
the separate archive, signature, notarization or installed-runtime acceptance.

## Documentation delivery

The branch is rebased onto the exact release commit. Before handoff, run the
ordinary commit hooks and privacy gate, push without force, open a docs-only
PR, and require successful devlog CI on its exact head. Merge and actual
installed-runtime acceptance remain with the coordinating task. No such
installation result is inferred from a public release or a successful build.

Only public repository content, public release metadata and the repository's
`AGENTS.md` were read.
No owner data, credentials, keys, application, models or tests
were accessed or run locally. The authorized delivery is a documentation
branch and PR; merge, release mutation and site publication are outside this
documentation task.
