# Releasing

***English** · [Русский](ru/RELEASING.md) · [中文](zh/RELEASING.md)*

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

`release-app` builds `Charoite.dmg` (the installer for a first install),
`Charoite.app.zip` (what the installed app updates from) and a `.sha256` for
both — without a published checksum the in-app update refuses to install
what it downloaded. It builds them on a macos runner and attaches
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

The embedded CPython that ships inside the bundle is downloaded from
python-build-standalone with a pinned version and build tag, and its sha256
is verified against the release's published `SHA256SUMS` before unpacking —
a mismatch (including a poisoned local cache) fails the build instead of
shipping an unverified interpreter.

Validation after changing any of this: download the asset, `ditto -x -k`,
`codesign -dv`, check `CFBundleShortVersionString` matches the tag.

## Signing and notarization

`release-app` signs and notarizes when — and only when — the secrets below
exist. Without them it builds ad-hoc exactly as before and prints a notice;
with the certificate but without notary secrets it prints a warning (a signed
but un-notarized bundle still trips Gatekeeper). Nothing turns red just
because the secrets are missing.

| Secret | What |
|---|---|
| `APPLE_DEVELOPER_ID_P12` | the *Developer ID Application* certificate with its private key, exported from Keychain Access as `.p12`, base64-encoded: `base64 -i developer-id.p12 \| pbcopy` |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | the password set on export |
| `APPLE_NOTARY_KEY_P8` | App Store Connect API key (`AuthKey_XXXXXXXXXX.p8`, Users and Access → Integrations → App Store Connect API → *Team keys*, role Developer is enough), base64-encoded |
| `APPLE_NOTARY_KEY_ID` | its Key ID |
| `APPLE_NOTARY_ISSUER_ID` | the Issuer ID shown on the same page |

Settings → Secrets and variables → Actions → New repository secret. The
certificate lands in a temporary keychain with a random password for the
duration of the run and is deleted in an `always()` step; the API key lives
in `$RUNNER_TEMP` and is removed the same way. The identity name («Developer
ID Application: <owner> (<team>)») is registered as a log mask and handed to
the build scripts through a file in `$RUNNER_TEMP` (`CHAROITE_SIGN_IDENTITY`
in their environment) — CI logs are public, and `spctl -vv` would otherwise
print the owner's name in them. It stays readable in the signature itself.

What the pipeline does with them (`app/make_app.sh`, `scripts/make_dmg.sh`,
`scripts/notarize.sh`):

1. every Mach-O inside the embedded python — found by header magic, not by
   file name or executable bit, so a `libfoo.so.1` with mode 644 is not
   missed — is signed one by one, libraries and wheel utilities first, then
   `bin/*`, with hardened runtime and a secure timestamp; the interpreter gets
   `app/Resources/entitlements/embedded-python.entitlements` (audio input,
   unsigned executable memory, library validation off). A single signing
   failure fails the build: an unsigned `.so` is a Gatekeeper rejection on
   the user's Mac, not a warning;
2. the bundle is signed with `app/Resources/entitlements/Charoite.entitlements`
   (audio input, calendars) and verified `--deep --strict`;
3. `Charoite.app.zip` is submitted with `notarytool submit --wait`, the ticket
   is stapled to the `.app`, and the zip is rebuilt from the stapled bundle
   (that zip is what the in-app updater installs — it must work offline);
4. `Charoite.dmg` is built, signed with a timestamp, notarized and stapled
   separately, then both `.sha256` files are recomputed — stapling changes
   the DMG, and the updater checks the published sums;
5. `spctl --assess` on the app and the DMG — the check Gatekeeper runs on
   the user's Mac.

A rejected notarization prints Apple's log: it names the file and the reason
(unsigned binary, no hardened runtime, no timestamp).

Why hardened runtime needs the python entitlements: notarization requires
hardened runtime on *every* executable in the bundle, and under it a child
process inherits nothing from the app — the daemon that reads the microphone
would get silence without a single error. Verified on a signed build:
`Contents/Resources/python/bin/python3` with `audio-input` records sound.
The first signed release still deserves a manual microphone check in the app.

The bundle must stay byte-for-byte what was notarized. The only thing that
used to write into it at runtime was the embedded python's `__pycache__`;
`AppDelegate.keepBundleSealed()` points `PYTHONPYCACHEPREFIX` at
`~/Library/Caches/ai.charoite.app/pycache` before the first child process
starts (regression test `BundleSealTests`). Check after any change that runs
python from the bundle: `codesign --verify --deep --strict` on an installed
copy that has been used — it must still pass.

Two things to know before adding the certificate:

- the owner name of the certificate is public — `codesign -dv` on the
  downloaded app shows «Developer ID Application: <name> (<team>)», and so do
  some system dialogs. An individual Apple Developer account puts a person's
  name there; an organization account puts the organization's;
- ad-hoc signed builds carry a designated requirement of `cdhash H"…"`, so
  every rebuild is a different app to macOS and permissions (microphone,
  system audio, calendar) are lost. Developer ID makes the requirement
  «identifier + team» — permissions survive updates.

Local builds behave the same: `app/make_app.sh` picks the Developer ID from
the login keychain if there is one, ad-hoc otherwise; `CHAROITE_SIGN_IDENTITY`
overrides the choice. A Developer ID build needs the network — every
signature fetches a timestamp from Apple; offline, build ad-hoc with
`CHAROITE_SIGN_IDENTITY=- app/make_app.sh`. The signing mode of each release
is appended to its release notes by the workflow — that is the line the
README refers to.

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

## Branch protection: what blocks a merge, and why

Required checks on `main` are **`lint`** and **`pytest (src/)`**. Two
deliberate choices behind that short list:

**Tests block merges now.** They did not before — required checks were
`lint` and `analyze`, so a red `pytest` merged without complaint. All 123
tests were advisory, including the guards that hold the privacy promises.

**`analyze` (CodeQL) is advisory, not required.** It runs on
`pull_request`, and GitHub creates no merge ref for a PR that conflicts
with `main` — so no `pull_request` workflow starts at all. The required
context then never arrives and the PR hangs forever on *"Expected —
Waiting for status to be reported"*, unfixable by re-running anything.
That was the whole story behind the "phantom checks" that used to be
cured by recreating the branch from `main`. `lint` and `pytest` also run
on `push`, so their contexts exist even on a conflicted PR.

**`strict` (require branches up to date) is off.** With four releases in
a day, every merge into `main` pushed every open PR into BEHIND, and
`required_linear_history` made the fix a rebase — new SHAs, all checks
re-run from scratch, fresh chance of conflict. It buys protection against
semantic conflicts, which nothing here implements anyway.

**`swift test (app)` and `build (app-ios)` stay advisory** while their
workflow keeps a `paths:` filter. A required check that never starts on
PRs which touch no Swift would hang them exactly like `analyze` did.

## Ручная приёмка перед релизом (экран и звук)

CI не воспроизводит ни живые устройства, ни конкуренцию с 30-гигабайтной
локальной моделью, поэтому релиз с изменениями живого контура или UI не
уходит без короткой ручной проверки на настоящей машине:

1. Старт записи → 2 минуты живой речи в оба канала → лента идёт, метки
   говорящих разумны, таймер тикает.
2. Один вопрос вслух → ⚡-подсказка приходит.
3. Стоп → минутки собраны, файлы записи на месте, уведомление пришло.
4. `tail -40 <данные>/logs/daemon.err.log` — без новых ошибок и
   `stt-health state=stalled`.

Что именно смотрелось — одной строкой в описание релизного PR. Изменения,
не трогающие экран/звук (доки, конвейер, скрипты), приёмки не требуют.

## Подпись манифеста обновлений (после каждого релиза)

Апдейтер не ставит выпуск без подписи манифеста ключом владельца — якоря,
независимого от GitHub (карточка №24): контрольная сумма лежит рядом с
архивом, и кто дотянулся до релиза, подписал бы и её. Ключ в CI не бывает.

После публикации релиза (release-please + release-app) — одна команда с
машины владельца:

    .venv/bin/python scripts/sign_release_manifest.py vX.Y.Z

Она скачивает `Charoite.app.zip`, распаковывает и СВЕРЯЕТ подпись бандла
(codesign --strict, команда AR7PDJQNR4) — подложенный до подписи чужой архив
получает отказ, а не подпись владельца. Затем строит манифест
`<версия>  <sha256>` САМА (версию несёт подписанный файл — голый хеш позволял
реплей старой тройки под новым тегом), подписывает его сырые байты
(raw ed25519 → base64) ключом `~/.config/charoite/update_manifest_ed25519.pem`
и прикладывает `Charoite.app.zip.manifest` + `.manifest.sig` к релизу.
Без этого шага пользователи увидят «у выпуска нет верной подписи манифеста».

Потерян приватный ключ — обновления встанут у всех: новую пару генерирует
владелец, публичная половина меняется в `UpdateAuthenticity.swift`
(константа `manifestKeyBase64`), выпускается новый релиз.
