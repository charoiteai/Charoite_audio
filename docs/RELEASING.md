# Releasing

Versions and `CHANGELOG.md` are automated by
[release-please](https://github.com/googleapis/release-please). You never edit
the changelog by hand — write `fix:`/`feat:` conventional commits, and the rest
happens on merge to `main`.

## How it works

1. Every push to `main` runs the `release-please` workflow.
2. It collects `fix:`/`feat:` commits since the last release into a **release
   PR** titled `chore(main): release X.Y.Z`, updating `CHANGELOG.md` and
   `.github/.release-please-manifest.json`.
3. Merging that PR tags the commit (`vX.Y.Z`) and creates a GitHub Release.

The current version lives in `.github/.release-please-manifest.json` — not in
a `version.txt` in the repo root. Git tags are the source of truth.

## One-time setup: RELEASE_PLEASE_TOKEN

The release PR must be created by a **personal access token**, not the built-in
`GITHUB_TOKEN`. GitHub deliberately does not run CI on branches created by the
built-in token (loop protection), so the release PR would sit `BLOCKED` with no
required checks. A PAT makes the branch "human", and `lint`/`analyze` run
normally.

To set it up (repo owner, once):

1. Create a **fine-grained PAT** scoped to this repo with:
   - **Contents: Read and write** (tag + changelog commit)
   - **Pull requests: Read and write** (open the release PR)
2. Add it as a repository secret named **`RELEASE_PLEASE_TOKEN`**
   (Settings → Secrets and variables → Actions → New repository secret).

The workflow falls back to `GITHUB_TOKEN` when the secret is absent, so nothing
breaks meanwhile — the release PR just needs a manual "Approve and run" until
the PAT is in place.

## App bundle on every release

`release-app` builds `Charoite.app.zip` on a macos runner and attaches
it to the release. Two triggers:

- `workflow_run` after the `release-please` workflow — the main path.
  release-please publishes releases with `GITHUB_TOKEN`, and GitHub's
  recursion guard means such events do NOT fire `release: published`
  in other workflows (v0.19.0 initially shipped without the bundle —
  that's how we learned). The chained job resolves the newest tag
  itself and exits quietly if the asset is already there.
- `release: published` — kept for human-created releases.

The bundle version comes from the latest git tag (`make_app.sh` does a
tag fetch), so a full checkout (`fetch-depth: 0`) is required in CI.
Validation after changing any of this: download the asset, `ditto -x -k`,
`codesign -dv`, check `CFBundleShortVersionString` matches the tag.
