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
# Релиз рождается pre-release (release-please-config) и становится latest
# только рукой владельца — после подписи. Апдейтер спрашивает /releases/latest,
# GitHub не отдаёт туда pre-release: выпуск без .manifest.sig для приложения
# не существует, вместо «у выпуска нет верной подписи» в рантайме.

def _fake_gh(calls: list, fail_on: str | None = None):
    def run(argv, **kw):
        calls.append(list(argv))
        sub = argv[2] if argv[:2] == ["gh", "release"] else argv[0]
        if sub == fail_on:
            raise subprocess.CalledProcessError(1, argv)
        if sub == "download":
            pathlib.Path(argv[argv.index("-O") + 1]).write_bytes(b"zip")
        return subprocess.CompletedProcess(argv, 0)
    return run


def _wire_main(monkeypatch, tmp_path, key, calls, fail_on=None):
    home = tmp_path / "home"
    (home / ".config" / "charoite").mkdir(parents=True)
    (home / ".config" / "charoite" / "gh_token").write_text("t\n")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(srm.subprocess, "run", _fake_gh(calls, fail_on))
    monkeypatch.setattr(srm, "verify_zip_is_ours", lambda z, u: None)
    monkeypatch.setattr(srm, "build_manifest", lambda tag, z: b"0.1.0  ab\n")
    monkeypatch.setattr(sys, "argv", ["sign_release_manifest.py", "v0.1.0",
                                      "--key", str(key)])


def test_release_is_promoted_to_latest_only_after_signature_upload(
        tmp_path, monkeypatch):
    key = _ephemeral_key(tmp_path)
    calls: list = []
    _wire_main(monkeypatch, tmp_path, key, calls)
    assert srm.main() == 0
    subs = [c[2] for c in calls if c[:2] == ["gh", "release"]]
    assert subs == ["download", "upload", "edit"], subs
    edit = calls[-1]
    assert edit[:3] == ["gh", "release", "edit"] and "v0.1.0" in edit
    assert "--prerelease=false" in edit and "--latest" in edit
    upload = next(c for c in calls if c[:3] == ["gh", "release", "upload"])
    assert any(p.endswith(f"{srm.ASSET}.sig") for p in upload), upload


def test_failed_signature_upload_leaves_release_as_prerelease(
        tmp_path, monkeypatch):
    """Упала загрузка подписи — релиз НЕ становится latest: иначе апдейтер
    увидит выпуск без .sig и откажет каждому пользователю."""
    key = _ephemeral_key(tmp_path)
    calls: list = []
    _wire_main(monkeypatch, tmp_path, key, calls, fail_on="upload")
    with pytest.raises(subprocess.CalledProcessError):
        srm.main()
    assert not any(c[:3] == ["gh", "release", "edit"] for c in calls)


def test_release_please_creates_prereleases():
    """Вторая половина гейта: без prerelease в конфиге release-please выпуск
    сразу становится latest — до подписи — и скрипт ничего не гейтит."""
    import json
    cfg = json.loads((ROOT / ".github" / "release-please-config.json")
                     .read_text(encoding="utf-8"))
    assert cfg["packages"]["."]["prerelease"] is True


def test_release_gate_tripwire_pre_major_versions_only():
    """Растяжка. release-please помечает выпуск pre-release по формуле
    `config.prerelease && (version.preRelease || version.major == 0)`
    (src/manifest.ts, buildReleases). На 0.x гейт держится на флаге конфига;
    с 1.0.0 флаг перестаёт действовать без prerelease-суффикса в версии, и
    неподписанный релиз снова станет latest до подписи. Тест падает на первой
    версии ≥ 1.0 — чтобы гейт переделали, а не потеряли молча."""
    import json
    manifest = json.loads((ROOT / ".github" / ".release-please-manifest.json")
                          .read_text(encoding="utf-8"))
    major = int(manifest["."].split(".")[0])
    assert major == 0, ("с 1.0 release-please перестанет помечать выпуск "
                        "pre-release — гейт подписи нужно перенести "
                        "(см. docs/RELEASING.md, «Подпись манифеста»)")
