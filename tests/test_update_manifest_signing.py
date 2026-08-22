"""Подпись манифеста обновлений: скрипт владельца и приложение говорят на
одном языке (карточка №24).

Схема одна на двоих: raw ed25519 над СЫРЫМИ байтами файла .sha256, подпись
в base64. Здесь скрипт подписывает одноразовым ключом, и подпись сверяется
той же криптографией, что у CryptoKit на стороне Swift.
"""
from __future__ import annotations

import base64
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sign_release_manifest as srm  # noqa: E402


def _ephemeral_key(tmp_path: pathlib.Path) -> pathlib.Path:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as s
    kp = tmp_path / "test_ed25519.pem"
    kp.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
    return kp


def test_signature_verifies_against_public_half(tmp_path):
    key = _ephemeral_key(tmp_path)
    manifest = b"abc123  Charoite.app.zip\n"
    sig = base64.b64decode(srm.sign_bytes(manifest, key))
    assert len(sig) == 64, "raw ed25519 — ровно 64 байта"

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(srm.public_key_base64(key)))
    pub.verify(sig, manifest)                 # неверная подпись бросила бы
    import pytest
    with pytest.raises(Exception):
        pub.verify(sig, b"evil")              # подмена манифеста ловится


def test_cli_signs_local_file_next_to_it(tmp_path):
    key = _ephemeral_key(tmp_path)
    m = tmp_path / "Charoite.app.zip.sha256"
    m.write_bytes(b"deadbeef  Charoite.app.zip\n")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sign_release_manifest.py"),
         "--file", str(m), "--key", str(key)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sig_file = tmp_path / "Charoite.app.zip.sha256.sig"
    assert sig_file.exists()
    assert len(base64.b64decode(sig_file.read_text())) == 64


def test_missing_key_is_a_loud_refusal(tmp_path):
    m = tmp_path / "m.sha256"
    m.write_bytes(b"x")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sign_release_manifest.py"),
         "--file", str(m), "--key", str(tmp_path / "нет-такого.pem")],
        capture_output=True, text=True)
    assert r.returncode == 2
    assert "якорь" in r.stderr


def test_built_in_swift_constant_matches_owner_key_format():
    """Публичный ключ в Swift-константе — валидный raw ed25519 (32 байта).
    Сверка с боевым приватным здесь невозможна и не нужна: боевой ключ не
    покидает машину владельца; форму держит и Swift-тест, этот — страховка
    от правки константы мимо обеих сторон."""
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services"
             / "UpdateAuthenticity.swift").read_text(encoding="utf-8")
    import re
    m = re.search(r'manifestKeyBase64 = "([^"]+)"', swift)
    assert m, "константа ключа не найдена"
    assert len(base64.b64decode(m.group(1))) == 32

def test_manifest_binds_version_to_content(tmp_path):
    """«<версия>  <хеш>» — голый хеш позволял реплей старой тройки под новым
    тегом (круг по PR #366, GLM + DeepSeek)."""
    z = tmp_path / "Charoite.app.zip"
    z.write_bytes(b"zip-bytes")
    m = srm.build_manifest("v0.57.0", z)
    version, digest = m.decode("ascii").split()
    assert version == "0.57.0"
    import hashlib
    assert digest == hashlib.sha256(b"zip-bytes").hexdigest()
    assert m.endswith(b"\n")


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="ditto/codesign — только macOS; скрипт подписи владельческий и живёт на маке")
def test_foreign_bundle_is_refused_before_signing(tmp_path):
    """Подменённый до шага подписи архив не должен получить подпись владельца:
    codesign-сверка обязана отказать на неподписанном/чужом бандле."""
    import subprocess
    import zipfile
    z = tmp_path / "Charoite.app.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Charoite.app/Contents/Info.plist", "<plist/>")
    import pytest
    with pytest.raises((subprocess.CalledProcessError, SystemExit)):
        srm.verify_zip_is_ours(z, tmp_path / "u")

@pytest.mark.skipif(sys.platform != "darwin",
                    reason="ditto/codesign — только macOS; скрипт подписи владельческий и живёт на маке")
def test_symlink_app_in_zip_is_refused(tmp_path):
    """Симлинк вместо Charoite.app обходил codesign-сверку: проверялась ЦЕЛЬ
    ссылки, а хеш подписывался от чужого архива (круг-2 по PR #366, DS)."""
    import stat
    import zipfile
    z = tmp_path / "Charoite.app.zip"
    with zipfile.ZipFile(z, "w") as f:
        zi = zipfile.ZipInfo("Charoite.app")
        zi.external_attr = (stat.S_IFLNK | 0o755) << 16
        f.writestr(zi, "/Applications/Charoite.app")
    import pytest
    with pytest.raises(SystemExit, match="симлинк"):
        srm.verify_zip_is_ours(z, tmp_path / "u")



# --- гейт неподписанного выпуска (проработка бэклога 22.08, Codex luna) ---
# Релиз рождается pre-release (release-please-config; release-app держит
# его pre-release после каждой пересборки) и становится latest только рукой
# владельца — после подписи. Апдейтер спрашивает /releases/latest, GitHub
# не отдаёт туда pre-release: выпуск без .manifest.sig для приложения не
# существует, вместо «у выпуска нет верной подписи» в рантайме.

def _fake_gh(calls: list, state: dict, fail_on: str | None = None):
    """Подобие gh с состоянием релиза: isPrerelease, assets, latest.
    Круг-1 (Codex): фейк без состояния проверял только порядок вызовов."""
    import json

    def run(argv, **kw):
        calls.append(list(argv))
        sub = argv[2] if argv[:2] == ["gh", "release"] else argv[0]
        if sub == fail_on:
            raise subprocess.CalledProcessError(1, argv)
        out = ""
        if sub == "view":
            if argv[3] == "--repo":                       # без тега — latest
                if state.get("api_down"):
                    return subprocess.CompletedProcess(
                        argv, 1, "", "error connecting to api.github.com")
                if state["latest"] is None:               # как у gh: 404
                    return subprocess.CompletedProcess(
                        argv, 1, "", "HTTP 404: Not Found (https://api.github.com/"
                        "repos/x/y/releases/latest)")
                out = state["latest"] + "\n"
            else:
                out = json.dumps({"isPrerelease": state["pre"],
                                  "assets": [{"name": n} for n in state["assets"]]})
        elif sub == "download":
            pathlib.Path(argv[argv.index("-O") + 1]).write_bytes(b"zip")
        elif sub == "upload":
            state["assets"] |= {pathlib.Path(p).name for p in argv
                                if p.endswith((".manifest", ".sig"))}
        elif sub == "edit":
            tag = argv[3]
            if "--prerelease" in argv:
                state["pre"] = True
                if state["latest"] == tag:                # спрятан — не latest
                    state["latest"] = state.get("prev_latest")
            if "--prerelease=false" in argv:
                state["pre"] = False
            if "--latest" in argv:
                state["latest"] = tag
        return subprocess.CompletedProcess(argv, 0, out, "")
    return run


def _wire_main(monkeypatch, tmp_path, key, calls, state, fail_on=None,
               tag="v0.1.0"):
    home = tmp_path / "home"
    (home / ".config" / "charoite").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "charoite" / "gh_token").write_text("t\n")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(srm.subprocess, "run", _fake_gh(calls, state, fail_on))
    monkeypatch.setattr(srm, "verify_zip_is_ours", lambda z, u: None)
    monkeypatch.setattr(srm, "build_manifest", lambda tag, z: b"0.1.0  ab\n")
    monkeypatch.setattr(sys, "argv", ["sign_release_manifest.py", tag,
                                      "--key", str(key)])


def _subs(calls):
    return [c[2] for c in calls if c[:2] == ["gh", "release"]]


def _edits(calls):
    return [c for c in calls if c[:3] == ["gh", "release", "edit"]]


def test_release_is_promoted_to_latest_only_after_signature_upload(
        tmp_path, monkeypatch):
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": {"Charoite.app.zip"}, "latest": "v0.0.9"}
    _wire_main(monkeypatch, tmp_path, key, calls, state)
    assert srm.main() == 0
    # view(состояние) → download → upload → view(latest) → edit: прятать
    # нечего, pre-release уже стоит
    assert _subs(calls) == ["view", "download", "upload", "view", "edit"], _subs(calls)
    upload = next(c for c in calls if c[:3] == ["gh", "release", "upload"])
    assert "--clobber" in upload, "без --clobber не перезаписать манифест"
    assert any(p.endswith(f"{srm.ASSET}.sig") for p in upload), upload
    (edit,) = _edits(calls)
    assert "v0.1.0" in edit and "--prerelease=false" in edit and "--latest" in edit
    assert state == {"pre": False, "latest": "v0.1.0",
                     "assets": {"Charoite.app.zip", srm.ASSET, f"{srm.ASSET}.sig"}}


def test_failed_signature_upload_leaves_release_as_prerelease(
        tmp_path, monkeypatch):
    """Упала загрузка подписи — релиз НЕ становится latest: иначе апдейтер
    увидит выпуск без .sig и откажет каждому пользователю."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": set(), "latest": None}
    _wire_main(monkeypatch, tmp_path, key, calls, state, fail_on="upload")
    with pytest.raises(SystemExit, match="повтори: sign_release_manifest.py v0.1.0"):
        srm.main()
    assert not _edits(calls)
    assert state["pre"] is True and state["latest"] is None


def test_failed_promotion_is_loud_and_leaves_prerelease(tmp_path, monkeypatch):
    """gh release edit упал после загрузки — не трейсбек, а внятная причина
    и команда для повтора; релиз остаётся pre-release (круг-1, qwen/DS)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": set(), "latest": None}
    _wire_main(monkeypatch, tmp_path, key, calls, state, fail_on="edit")
    with pytest.raises(SystemExit, match="остался pre-release"):
        srm.main()
    assert "upload" in _subs(calls)
    assert state["pre"] is True


def test_stable_unsigned_release_is_signed_but_gate_failure_is_loud(
        tmp_path, monkeypatch, capsys):
    """Гейт не сработал (ручной стабильный релиз, флаг снят рукой): подпись
    нужна немедленно — пользователи и так заблокированы, — но скрипт
    прячет релиз на время замены пары, кричит и выходит с кодом 3
    (круг-1, Codex Critical 1 / DeepSeek I1)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": False, "assets": {"Charoite.app.zip"}, "latest": "v0.1.0"}
    _wire_main(monkeypatch, tmp_path, key, calls, state)
    assert srm.main() == 3
    hide, promote = _edits(calls)
    assert "--prerelease" in hide and "--prerelease=false" not in hide
    assert _subs(calls).index("edit") < _subs(calls).index("upload"), \
        "прятать надо ДО замены пары"
    assert "--prerelease=false" in promote and "--latest" in promote
    assert "ГЕЙТ НЕ СРАБОТАЛ" in capsys.readouterr().err
    assert state["pre"] is False and f"{srm.ASSET}.sig" in state["assets"]


def test_resigning_latest_release_hides_it_while_assets_change(
        tmp_path, monkeypatch):
    """Повторная подпись latest-релиза: два upload не атомарны — между ними
    апдейтер видел бы манифест с чужой подписью (круг-1, Codex I1)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": False, "latest": "v0.1.0", "prev_latest": "v0.0.9",
                        "assets": {"Charoite.app.zip", srm.ASSET, f"{srm.ASSET}.sig"}}
    _wire_main(monkeypatch, tmp_path, key, calls, state)
    assert srm.main() == 0
    assert _subs(calls) == ["view", "edit", "download", "upload", "view", "edit"]
    assert state["pre"] is False and state["latest"] == "v0.1.0"


def test_signing_older_tag_does_not_move_latest_backwards(tmp_path, monkeypatch):
    """Подпись исторического v0.1.0 при latest v0.2.0: релиз становится
    стабильным, но --latest не ставится (круг-1, DeepSeek M5 / Codex I2)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": set(), "latest": "v0.2.0"}
    _wire_main(monkeypatch, tmp_path, key, calls, state)
    assert srm.main() == 0
    (edit,) = _edits(calls)
    assert "--prerelease=false" in edit and "--latest" not in edit
    assert state == {"pre": False, "latest": "v0.2.0",
                     "assets": {srm.ASSET, f"{srm.ASSET}.sig"}}


def test_failed_hide_is_loud_and_release_stays_as_it_was(tmp_path, monkeypatch):
    """Гейт не сработал, а спрятать релиз не вышло — сказать об этом, а не
    трейсбек; ничего не качать и не заливать (круг-2, Codex I6)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": False, "assets": {"Charoite.app.zip"}, "latest": "v0.1.0"}
    _wire_main(monkeypatch, tmp_path, key, calls, state, fail_on="edit")
    with pytest.raises(SystemExit, match="остался latest"):
        srm.main()
    assert "download" not in _subs(calls) and "upload" not in _subs(calls)


def test_api_error_on_latest_lookup_is_not_treated_as_no_latest(
        tmp_path, monkeypatch):
    """Сеть/токен/5xx при чтении latest — не «latest нет»: иначе исторический
    тег получил бы --latest (круг-2, Codex I5). 404 — честное «нет»."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": set(), "latest": "v0.2.0", "api_down": True}
    _wire_main(monkeypatch, tmp_path, key, calls, state)
    with pytest.raises(SystemExit, match="не удалось узнать нынешний latest"):
        srm.main()
    assert not _edits(calls) and state["pre"] is True


def test_prerelease_suffix_never_becomes_latest(tmp_path, monkeypatch):
    """v0.1.0-rc.1 при latest v0.1.0: числа равны, но RC ниже релиза —
    стабильным станет, latest — нет (круг-2, Codex I7)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": set(), "latest": "v0.1.0"}
    _wire_main(monkeypatch, tmp_path, key, calls, state, tag="v0.1.0-rc.1")
    assert srm.main() == 0
    (edit,) = _edits(calls)
    assert "--prerelease=false" in edit and "--latest" not in edit
    assert state["latest"] == "v0.1.0"


def test_half_a_pair_counts_as_unsigned(tmp_path, monkeypatch, capsys):
    """Стабильный релиз с одним .sig без .manifest — не подписан: приложению
    нужны оба файла (круг-2, Codex I3)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": False, "latest": "v0.1.0",
                        "assets": {"Charoite.app.zip", f"{srm.ASSET}.sig"}}
    _wire_main(monkeypatch, tmp_path, key, calls, state)
    assert srm.main() == 3
    assert "ГЕЙТ НЕ СРАБОТАЛ" in capsys.readouterr().err
    assert state["assets"] >= {srm.ASSET, f"{srm.ASSET}.sig"}


def test_unknown_tag_is_a_loud_refusal(tmp_path, monkeypatch):
    """Опечатка в теге — причина от gh, а не трейсбек с потерянным stderr
    (круг-2, Sonnet)."""
    key = _ephemeral_key(tmp_path)
    calls, state = [], {"pre": True, "assets": set(), "latest": None}
    _wire_main(monkeypatch, tmp_path, key, calls, state, fail_on="view",
               tag="v9.9.9")
    with pytest.raises(SystemExit, match="не удалось прочитать состояние релиза"):
        srm.main()
    assert _subs(calls) == ["view"]


def test_version_tuple_orders_tags():
    vt = srm.version_tuple
    assert vt("v0.58.0") == (0, 58, 0) and vt("0.58.0") == (0, 58, 0)
    assert vt("v0.9.1") < vt("v0.10.0") < vt("v1.0.0")
    assert srm.has_prerelease_suffix("v0.58.0-rc.1")
    assert not srm.has_prerelease_suffix("v0.58.0")


def test_release_please_config_requests_prereleases():
    """Первая половина гейта: без prerelease в конфиге release-please
    выпуск сразу становится latest — до подписи. Проверяется конфиг, не
    поведение release-please; поведение страхуют release-app (гейт в
    Resolve/Attach) и release_state() в скрипте."""
    import json
    cfg = json.loads((ROOT / ".github" / "release-please-config.json")
                     .read_text(encoding="utf-8"))
    assert cfg["packages"]["."]["prerelease"] is True


def test_release_app_workflow_keeps_unsigned_release_prerelease():
    """Вторая половина гейта — на стороне CI: после каждой сборки релиз
    pre-release, устаревшая пара .manifest/.sig снята, стабильный без
    подписи переводится в pre-release; CI не кладёт .manifest без подписи
    (круг-1, Codex Critical 2). Строковые проверки: workflow в тестах не
    запустить, но молча потерять шаги нельзя."""
    yml = (ROOT / ".github" / "workflows" / "release-app.yml").read_text(encoding="utf-8")
    assert "types: [published, released]" in yml
    hide = 'gh release edit "$TAG" --repo "$REPO" --prerelease\n'
    resolve = yml[yml.index("name: Resolve tag and decide"):yml.index("uses: actions/checkout@")]
    attach = yml[yml.index("name: Attach to release"):yml.index("name: Remove signing material")]
    assert hide in resolve, "гейт в Resolve: ручной стабильный без подписи"
    # Attach: спрятать ДО замены архива — упадёт upload/delete, релиз
    # останется pre-release, а не стабильным с битой парой (круг-2, DS I1)
    assert attach.index(hide) < attach.index("gh release upload"), \
        "в Attach pre-release ставится до upload"
    assert 'gh release delete-asset "$TAG" "$stale"' in attach
    assert "> app/build/Charoite.app.zip.manifest" not in yml, \
        "манифест без подписи CI класть не должен"
    # подписан = оба файла пары, точные имена; проверки без pipe (pipefail)
    assert "has Charoite.app.zip.manifest && has Charoite.app.zip.manifest.sig" in resolve
    import re
    assert not re.search(r"\|\s*grep", resolve + attach), "pipe в grep под pipefail"


def test_release_gate_tripwire_pre_major_versions_only():
    """Растяжка. release-please помечает выпуск pre-release по формуле
    `config.prerelease && (version.preRelease || version.major == 0)`
    (src/manifest.ts, buildReleases). На 0.x гейт без окна; с 1.0.0 флаг
    перестаёт действовать, и остаётся только CI-гейт release-app — с окном
    в латентность Actions между публикацией и переводом в pre-release.
    Тест падает на первой версии ≥ 1.0 — чтобы окно приняли осознанно или
    перенесли гейт, а не потеряли молча."""
    import json
    manifest = json.loads((ROOT / ".github" / ".release-please-manifest.json")
                          .read_text(encoding="utf-8"))
    major = int(manifest["."].split(".")[0])
    assert major == 0, ("с 1.0 release-please перестанет помечать выпуск "
                        "pre-release — решить про окно CI-гейта "
                        "(см. docs/RELEASING.md, «Подпись манифеста»)")
