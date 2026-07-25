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

## Squash merges: the PR title IS the commit

With squash merge, `main` receives exactly one commit whose subject is the
**PR title**. If that title is not a conventional commit (`fix: …`,
`feat: …`), release-please does not see the work at all: no release PR, no
CHANGELOG entry, no version bump. This bit us four times in one day
(#83–#86 all merged as `Fix/<branch name>`), leaving a day of shipped
fixes invisible to the changelog.

Rules:

1. Edit the title in the merge dialog to conventional form before
   confirming the squash.
2. One PR with several user-visible changes: add extra plain
   `fix(scope): …` lines to the squash **body** (no `* ` bullets —
   bulleted lines are not parsed) — release-please registers each line
   as its own entry.
3. Already merged with a bad title: push a carrier commit whose message
   holds the missed conventional lines (an empty commit from a
   maintainer's machine, or a small real change through a PR).

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
it to the release. Three triggers:

- `workflow_run` after the `release-please` workflow — the main path.
  release-please publishes releases with `GITHUB_TOKEN`, and GitHub's
  recursion guard means such events do NOT fire `release: published`
  in other workflows (v0.19.0 initially shipped without the bundle —
  that's how we learned). The job only proceeds when release-please
  **succeeded** (`completed` also arrives after a failed run).
- `release: published` — kept for human-created releases.
- `workflow_dispatch` with a `tag` input — manual re-upload for an old
  release (see the v0.19.0 postmortem below). Manual runs always rebuild
  and `--clobber` the asset.

The order inside the job is deliberate: the **first** step resolves which
tag needs an asset and whether one already exists — before anything is
built. `release-please` completes on every push to `main`, usually without
creating a release, so most chained runs must end in seconds at the
resolve step, not after a full macOS build.

The build checks out `refs/tags/<tag>` — **the code of that release, not
the tip of `main`**. `make_app.sh` stamps the version from `git describe`,
which on a tag checkout is exactly the tag, so `CFBundleShortVersionString`
matches the release. A full checkout (`fetch-depth: 0`) is required for
`git describe` to see tags.

Validation after changing any of this: download the asset, `ditto -x -k`,
`codesign -dv`, check `CFBundleShortVersionString` matches the tag.

## Postmortem: the v0.19.0 double

Two defects met on one release:

1. v0.19.0 shipped without a bundle (the recursion-guard lesson above).
2. The first fix resolved the tag as `gh release list --limit 1` but built
   the **current `main`** — the checkout had no `ref`. By the time the fix
   itself was merged, `main` was already ahead of the `v0.19.0` tag, so a
   build of newer code was attached to the old tag. Users downloading
   "0.19.0" got a bundle whose code was newer than the release, with
   `Info.plist` claiming 0.19.0 all along — `git describe` on `main`
   still resolved to the last tag.

The invariant that fell out of this — *an asset is built from the code of
its own tag* — is now enforced by `tests/test_workflows.py`, and the
workflow checks out the tag explicitly.

**Re-uploading a correct 0.19.0 asset:** Actions → release-app →
Run workflow → `tag: v0.19.0`. The manual run rebuilds from the
`v0.19.0` tag and replaces the wrong asset (`--clobber`). Then validate
as above: the unzipped app must report `CFBundleShortVersionString`
0.19.0.
