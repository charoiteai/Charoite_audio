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
    try:
        prerelease, signed = release_state(a.tag, env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"{a.tag}: не удалось прочитать состояние релиза (gh release view: "
            f"код {e.returncode}): {(e.stderr or '').strip()}") from e
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
        try:
            hide_release(a.tag, env)
        except subprocess.CalledProcessError as e:
            raise SystemExit(
                f"{a.tag}: не удалось перевести релиз в pre-release "
                f"(gh release edit: код {e.returncode}) — релиз как был, так и "
                f"остался latest; повтори позже: sign_release_manifest.py {a.tag}"
            ) from e
    try:
        sign_and_upload(a.tag, key, env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"{a.tag}: шаг `{' '.join(map(str, e.cmd[:3]))}` упал (код "
            f"{e.returncode}); релиз остался pre-release — повтори: "
            f"sign_release_manifest.py {a.tag}") from e
    # Подпись на месте — релиз становится стабильным. latest — только если
    # тег не старше нынешнего latest и без суффикса версии: подпись
    # исторического v0.57.0 после v0.58.0 не должна откатывать
    # /releases/latest, а v0.58.0-rc.1 не должен обгонять v0.58.0
    # (круг-1 DeepSeek/Codex, круг-2 Codex).
    newest = latest_stable_tag(env)
    make_latest = (newest is None or version_tuple(a.tag) >= version_tuple(newest)) \
        and not has_prerelease_suffix(a.tag)
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


def sign_and_upload(tag: str, key: pathlib.Path, env: dict) -> None:
    """Скачать архив, сверить по codesign, построить и подписать манифест,
    приложить пару к релизу. Любой сбой — CalledProcessError/SystemExit."""
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        zip_path = tdp / ZIP
        subprocess.run(["gh", "release", "download", tag, "--repo", REPO,
                        "-p", ZIP, "-O", str(zip_path), "--clobber"],
                       check=True, env=env)
        # чужому бандлу — отказ ДО подписи; манифест строим сами из архива
        verify_zip_is_ours(zip_path, tdp / "unpacked")
        manifest = build_manifest(tag, zip_path)
        m = tdp / ASSET
        m.write_bytes(manifest)
        print("подписываю:", manifest.decode("ascii").strip())
        sig_path = tdp / f"{ASSET}.sig"
        sig_path.write_text(sign_bytes(manifest, key) + "\n", encoding="ascii")
        subprocess.run(["gh", "release", "upload", tag, "--repo", REPO,
                        str(m), str(sig_path), "--clobber"], check=True, env=env)


def release_state(tag: str, env: dict) -> tuple[bool, bool]:
    """(pre-release?, подпись манифеста уже приложена?) по данным GitHub."""
    r = subprocess.run(["gh", "release", "view", tag, "--repo", REPO,
                        "--json", "isPrerelease,assets"],
                       check=True, env=env, capture_output=True, text=True)
    d = json.loads(r.stdout)
    names = {a.get("name") for a in d.get("assets", [])}
    # Подписан — когда на месте ОБА файла пары: приложению нужны оба.
    return bool(d.get("isPrerelease")), {ASSET, f"{ASSET}.sig"} <= names


def latest_stable_tag(env: dict) -> str | None:
    """Тег нынешнего /releases/latest; None — стабильных релизов нет."""
    r = subprocess.run(["gh", "release", "view", "--repo", REPO,
                        "--json", "tagName", "-q", ".tagName"],
                       check=False, env=env, capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout.strip() or None
    # Нет ни одного стабильного релиза — GitHub отвечает 404. Всё остальное
    # (сеть, токен, 5xx) — не знание «latest нет», а незнание: с ним нельзя
    # ставить --latest историческому тегу (круг-2, Codex).
    if "404" in r.stderr or "not found" in r.stderr.lower():
        return None
    raise SystemExit(f"не удалось узнать нынешний latest (gh release view: "
                     f"код {r.returncode}): {r.stderr.strip()}; повтори: "
                     f"sign_release_manifest.py <тег>")


def version_tuple(tag: str) -> tuple[int, ...]:
    """v0.58.0 → (0, 58, 0); суффикс не учитывается — см. has_prerelease_suffix."""
    return tuple(int(x) for x in re.findall(r"\d+", tag)[:3])


def has_prerelease_suffix(tag: str) -> bool:
    """v0.58.0-rc.1 → True: такой тег не бывает latest (SemVer: pre-release
    ниже релиза той же версии). release-please simple суффиксов не даёт,
    это защита от ручного тега."""
    return "-" in tag.removeprefix("v")


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
