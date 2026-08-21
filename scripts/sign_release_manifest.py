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
import os
import pathlib
import subprocess
import sys
import tempfile

KEY = pathlib.Path.home() / ".config" / "charoite" / "update_manifest_ed25519.pem"
ASSET = "Charoite.app.zip.sha256"
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
    with tempfile.TemporaryDirectory() as td:
        m = pathlib.Path(td) / ASSET
        subprocess.run(["gh", "release", "download", a.tag, "--repo", REPO,
                        "-p", ASSET, "-O", str(m), "--clobber"],
                       check=True, env=env)
        sig_path = pathlib.Path(td) / f"{ASSET}.sig"
        sig_path.write_text(sign_bytes(m.read_bytes(), key) + "\n", encoding="ascii")
        subprocess.run(["gh", "release", "upload", a.tag, "--repo", REPO,
                        str(sig_path), "--clobber"], check=True, env=env)
    print(f"{a.tag}: манифест подписан, {ASSET}.sig загружен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
