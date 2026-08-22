#!/usr/bin/env python3
"""Подписать манифест релиза ключом владельца и приложить подпись к релизу.

Якорь подлинности обновлений, НЕЗАВИСИМЫЙ от GitHub (карточка №24):
контрольная сумма лежит рядом с архивом, и кто дотянулся до релиза
(утёкший токен CI, компрометация аккаунта), тот подписал бы и её. Этот
ключ в CI не бывает: приватная половина живёт только у владельца в
`~/.config/charoite/update_manifest_ed25519.pem`, подпись делается локально
после каждого релиза — шаг записан в docs/RELEASING.md. Приложение
(UpdateAuthenticity.swift) требует эту подпись перед подменой бандла.

    .venv/bin/python scripts/sign_release_manifest.py v0.57.0
    .venv/bin/python scripts/sign_release_manifest.py --file m.sha256  # без gh

Подпись — raw ed25519 над СЫРЫМИ байтами файла .sha256, в base64: ровно то,
что проверяет CryptoKit на стороне приложения.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

KEY = pathlib.Path.home() / ".config" / "charoite" / "update_manifest_ed25519.pem"
ZIP = "Charoite.app.zip"
ASSET = ZIP + ".manifest"
TEAM_REQUIREMENT = ('anchor apple generic and '
                    'certificate leaf[subject.OU] = "AR7PDJQNR4"')
REPO = "charoiteai/Charoite_audio"


def sign_bytes(manifest: bytes, key_path: pathlib.Path = KEY) -> str:
    """base64 raw-подписи ed25519 (64 байта) над сырыми байтами манифеста."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(key_path.read_bytes(), password=None)
    return base64.b64encode(key.sign(manifest)).decode("ascii")


def public_key_base64(key_path: pathlib.Path = KEY) -> str:
    """Публичная половина — сверить с константой в UpdateAuthenticity.swift."""
    from cryptography.hazmat.primitives import serialization as s
    key = s.load_pem_private_key(key_path.read_bytes(), password=None)
    raw = key.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def build_manifest(tag: str, zip_path: pathlib.Path) -> bytes:
    """«<версия>  <sha256>\n» — версию несёт сам подписанный файл.

    Голый хеш позволял реплей: старая честная тройка под новым тегом
    проходила все проверки (круг по PR #366, GLM + DeepSeek).
    """
    import hashlib
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    version = tag[1:] if tag.startswith("v") else tag
    return f"{version}  {digest}\n".encode("ascii")


def verify_zip_is_ours(zip_path: pathlib.Path, workdir: pathlib.Path) -> None:
    """Codesign-сверка АРХИВА до подписи манифеста.

    Скрипт подписывал то, что лежит на релизе, не глядя: подменённый до
    шага подписи архив получил бы честную подпись владельца (круг по
    PR #366, DeepSeek). Распаковываем и требуем строгую подпись нашей
    команды — чужой бандл сюда не проходит.
    """
    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(workdir)],
                   check=True)
    app = workdir / "Charoite.app"
    # is_dir() и codesign идут за симлинком — подписали бы хеш чужого архива,
    # сверив ЦЕЛЬ ссылки, например уже установленный /Applications/Charoite.app
    # (круг-2 по PR #366, DeepSeek).
    if app.is_symlink() or not app.is_dir():
        raise SystemExit("в архиве Charoite.app — не каталог приложения "
                         "(симлинк?) — подписывать нечего")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict",
                    "-R", "=" + TEAM_REQUIREMENT, str(app)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tag", nargs="?", help="тег релиза (vX.Y.Z) — скачать, подписать, загрузить")
    ap.add_argument("--file", help="подписать локальный файл: подпись — рядом, в .sig")
    ap.add_argument("--key", default=str(KEY), help="путь к приватному ключу (для тестов)")
    a = ap.parse_args()
    key = pathlib.Path(a.key).expanduser()
    if not key.exists():
        print(f"нет ключа {key} — якорь не может быть подписан", file=sys.stderr)
        return 2

    if a.file:
        src = pathlib.Path(a.file)
        sig = sign_bytes(src.read_bytes(), key)
        out = src.with_suffix(src.suffix + ".sig")
        out.write_text(sig + "\n", encoding="ascii")
        print(f"подпись: {out}")
        return 0

    if not a.tag:
        ap.error("нужен тег релиза или --file")
    token = (pathlib.Path.home() / ".config" / "charoite" / "gh_token").read_text().strip()
    env = dict(os.environ, GH_TOKEN=token)
    # Гейт (PR #375): latest без подписи для приложения существовать не
    # должен. Нормальный путь — релиз уже pre-release (release-please +
    # release-app). Стабильный и подписанный — повторная подпись: на время
    # замены пары прячем, иначе между двумя загрузками апдейтер видит
    # манифест с чужой подписью. Стабильный и НЕподписанный — гейт не
    # сработал (ручной релиз, флаг снят рукой): подписываем всё равно —
    # пользователи и так заблокированы, — но кричим и выходим с кодом 3.
    prerelease, signed = release_state(a.tag, env)
    gate_failed = False
    if not prerelease:
        if signed:
            print(f"{a.tag}: уже latest и подписан — повторная подпись, "
                  "на время загрузки прячу в pre-release")
        else:
            print(f"ГЕЙТ НЕ СРАБОТАЛ: {a.tag} опубликован стабильным без подписи "
                  "манифеста — до этой минуты апдейтер отдавал его пользователям. "
                  "Подписываю, но разберись, откуда релиз (ручной? флаг снят?)",
                  file=sys.stderr)
            gate_failed = True
        hide_release(a.tag, env)
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        zip_path = tdp / ZIP
        subprocess.run(["gh", "release", "download", a.tag, "--repo", REPO,
                        "-p", ZIP, "-O", str(zip_path), "--clobber"],
                       check=True, env=env)
        # чужому бандлу — отказ ДО подписи; манифест строим сами из архива
        verify_zip_is_ours(zip_path, tdp / "unpacked")
        manifest = build_manifest(a.tag, zip_path)
        m = tdp / ASSET
        m.write_bytes(manifest)
        print("подписываю:", manifest.decode("ascii").strip())
        sig_path = tdp / f"{ASSET}.sig"
        sig_path.write_text(sign_bytes(manifest, key) + "\n", encoding="ascii")
        subprocess.run(["gh", "release", "upload", a.tag, "--repo", REPO,
                        str(m), str(sig_path), "--clobber"], check=True, env=env)
    # Подпись на месте — релиз становится стабильным. latest — только если
    # тег не старше нынешнего latest: подпись исторического v0.57.0 после
    # v0.58.0 не должна откатывать /releases/latest назад (круг-1, DeepSeek
    # и Codex).
    newest = latest_stable_tag(env)
    make_latest = newest is None or version_tuple(a.tag) >= version_tuple(newest)
    try:
        promote_release(a.tag, env, make_latest=make_latest)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"{a.tag}: подпись загружена, но снять pre-release не удалось "
            f"(gh release edit: код {e.returncode}); релиз остался pre-release — "
            f"повтори: gh release edit {a.tag} --repo {REPO} --prerelease=false"
            f"{' --latest' if make_latest else ''}") from e
    print(f"{a.tag}: архив сверен по codesign, манифест подписан, "
          f"{ASSET} и {ASSET}.sig загружены, релиз стабильный"
          + (", latest" if make_latest else f" (latest остаётся {newest})"))
    return 3 if gate_failed else 0


def release_state(tag: str, env: dict) -> tuple[bool, bool]:
    """(pre-release?, подпись манифеста уже приложена?) по данным GitHub."""
    r = subprocess.run(["gh", "release", "view", tag, "--repo", REPO,
                        "--json", "isPrerelease,assets"],
                       check=True, env=env, capture_output=True, text=True)
    d = json.loads(r.stdout)
    names = {a.get("name") for a in d.get("assets", [])}
    return bool(d.get("isPrerelease")), f"{ASSET}.sig" in names


def latest_stable_tag(env: dict) -> str | None:
    """Тег нынешнего /releases/latest; None — стабильных релизов нет."""
    r = subprocess.run(["gh", "release", "view", "--repo", REPO,
                        "--json", "tagName", "-q", ".tagName"],
                       check=False, env=env, capture_output=True, text=True)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def version_tuple(tag: str) -> tuple[int, ...]:
    """v0.58.0 → (0, 58, 0); всё нечисловое после цифр отбрасывается."""
    return tuple(int(x) for x in re.findall(r"\d+", tag)[:3])


def hide_release(tag: str, env: dict) -> None:
    """Перевести релиз в pre-release — убрать из /releases/latest."""
    subprocess.run(["gh", "release", "edit", tag, "--repo", REPO,
                    "--prerelease"], check=True, env=env)


def promote_release(tag: str, env: dict, make_latest: bool = True) -> None:
    """Снять pre-release (и объявить latest) — только после подписи."""
    cmd = ["gh", "release", "edit", tag, "--repo", REPO, "--prerelease=false"]
    if make_latest:
        cmd.append("--latest")
    subprocess.run(cmd, check=True, env=env)


if __name__ == "__main__":
    sys.exit(main())
