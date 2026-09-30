#!/usr/bin/env python3
"""Страж обезличивания публичного репозитория.

Раньше жил только в .git/hooks/pre-commit — а git хуки не переносит: на новой
машине, в свежем клоне и у любого контрибьютора защиты не было вовсе. Теперь
он в репозитории и подключён через .pre-commit-config.yaml.

Сам список маркеров приватен и лежит вне git (~/.config/charoite/private_markers.txt):
перечень того, что нельзя публиковать, сам по себе — чувствительные данные.

Почему Python, а не grep: коротким аббревиатурам нужны границы слова, иначе
трёхбуквенный маркер находится внутри обычных слов («слЕПАя зона») и страж
блокирует коммит на ровном месте. А `\\b` понимает GNU grep, но не BSD на
macOS; `[[:<:]]` — наоборот. Страж, который врёт, быстро начинают обходить.

Режимы (№541): без флагов — pre-commit; `--all` и `--public-only` — дерево;
`--range`, `--pre-push`, `--range-from-env` — каждый уходящий коммит: промежуточный
коммит публичного репозитория опубликован навсегда, даже если следующий его
исправил, поэтому судится не итоговый дифф, а каждый коммит набора.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import pathlib
import re
import subprocess
import sys
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

# До этой длины маркер считается аббревиатурой и ищется по границам слова.
SHORT_MARKER = 4
# Публичный репозиторий коммитится под одним именем.
MAINTAINER_EMAIL = "charoiteai@gmail.com"


def default_markers_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "charoite" / "private_markers.txt"


def markers_path() -> pathlib.Path:
    env = os.environ.get("CHAROITE_MARKERS")
    if env:
        return pathlib.Path(env)
    return default_markers_path()


def nfc(text: str) -> str:
    """Одна форма Юникода для маркеров и судимого текста: «й» и «ё» в NFD (тексты
    с HFS+, имена из Finder) — два символа, регэксп их с NFC не сравнит."""
    return unicodedata.normalize("NFC", text)


def load_markers(path: pathlib.Path) -> list[str]:
    """Единственный читатель списка: пустые строки и `#`-комментарии — не маркеры."""
    return [nfc(ln.strip()) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def build_pattern(markers: list[str]) -> re.Pattern[str]:
    parts = []
    for m in markers:
        esc = re.escape(m)
        parts.append(rf"\b{esc}\b" if len(m) <= SHORT_MARKER else esc)
    return re.compile("|".join(parts), re.IGNORECASE)


class GitError(Exception):
    """git ответил ошибкой. Страж её не глотает: пустой вывод упавшего git —
    не «чисто», а «проверить не смог» (круг 1 №541, Sonnet C1)."""


# Настройки владельца не должны менять то, что страж читает: кавычки в
# кириллических путях, цвет, скрытый дифф корневого коммита.
# `--no-replace-objects`: refs/replace меняет то, что показывает log, но не то,
# что уходит push-ем — страж обязан видеть настоящие объекты.
_GIT = ("git", "--no-replace-objects", "-c", "core.quotepath=false", "-c", "color.ui=never",
        "-c", "log.showRoot=true")


def _clip(text: str, limit: int = 60) -> str:
    """Кусок чужого текста для сообщения об ошибке: не больше `limit` знаков."""
    return text[:limit]


def git(*args: str, stdin: bytes | None = None) -> bytes:
    p = subprocess.run([*_GIT, *args], capture_output=True, input=stdin)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args[:2])}: {_clip(err, 300) or f'код {p.returncode}'}")
    return p.stdout


def git_ok(*args: str) -> bool:
    """Вопрос к git, где отказ — ответ «нет», а не ошибка."""
    return subprocess.run([*_GIT, *args], capture_output=True).returncode == 0


def tracked_files() -> list[pathlib.Path]:
    """Файлы под учётом git — то, что уже опубликовано."""
    out = git("ls-files", "-z").decode("utf-8", "replace")
    return [pathlib.Path(p) for p in out.split("\0") if p]


SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
               ".zip", ".onnx", ".wav", ".m4a", ".mp3"}


def scan_files(pattern: re.Pattern[str], files: list[pathlib.Path]) -> list[str]:
    """Где в этих файлах маркеры. Возвращает «путь:строка» — без цитаты.

    Диф страж показывает строкой: там она ещё не опубликована и автору нужно
    видеть, что именно он пишет. Для файлов, которые УЖЕ в репозитории, вывод
    попадает в логи CI и чужие терминалы, поэтому здесь только место — автор
    откроет файл сам.
    """
    hits: list[str] = []
    for f in files:
        if f.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue        # бинарь или удалённый файл — не наша забота
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(nfc(line)):
                hits.append(f"{f}:{i}")
    return hits


# Второй рубеж: форматы, а не имена.
#
# Список поимённых маркеров приватен и живёт только на машине автора — в CI
# его нет и быть не должно: перечень того, что мы прячем, сам по себе
# чувствителен, а секреты GitHub вдобавок не отдаются в PR из форков, то
# есть проверка не сработала бы ровно в самом опасном случае.
#
# Поэтому здесь — публичные шаблоны, которые ничего не выдают своим видом,
# но ловят самый частый способ утечки: скопированный кусок конфига, лога или
# пути с рабочей машины. Это ВТОРОЙ рубеж, а не замена первому: фамилию
# коллеги в комментарии поймает только локальный хук.
#
# Синтетические имена («a», «user», «test») пропускаем: примеры и тесты
# обязаны показывать пути, а страж, который ругается на документацию,
# начинает восприниматься как шум.
# Пометка строки, которой разрешено выглядеть как утечка.
PUBLIC_ALLOW = "приватный-образец"
FAKE_USER = r"(?!a/|x/|user/|test/|someone/|you/|me/|ПУТЬ/)"
PUBLIC_PATTERNS: dict[str, str] = {
    "внутренний хост": r"[\w.-]+\.(corp|intranet|internal|lan)\b|[\w-]+-gw-[\w.-]+",
    "почта на непубличном домене": r"[\w.+-]+@[\w-]+\.(ru|local|corp|lan)\b",
    # первая буква любая: имя с заглавной и кириллица — тоже личный путь (аудит 13.09, GLM M6)
    "личный путь": rf"/Users/{FAKE_USER}[A-Za-zА-Яа-яЁё][\w-]*/",
    "фамилия с инициалами": r"[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.",
}


def public_hits(line: str) -> list[str]:
    """Какие публичные форматы сработали на строке (пометка — пропуск). Строка —
    уже в NFC (зовёт `kinds` в `scan_commits`)."""
    # Явная пометка в самой строке, а не исключённый файл: тесты этого стража
    # обязаны содержать образцы утечек, но глушить файл целиком — значит
    # открыть место, где можно спрятать что угодно. Пометка видна в ревью построчно.
    if PUBLIC_ALLOW in line:
        return []
    return [name for name, raw in PUBLIC_PATTERNS.items() if re.search(raw, line)]


def scan_public(files: list[pathlib.Path]) -> list[str]:
    """Находки по публичным шаблонам — «путь:строка: чем сработало»."""
    hits: list[str] = []
    for name, raw in PUBLIC_PATTERNS.items():
        rx = re.compile(raw)
        for f in files:
            if f.suffix.lower() in SKIP_SUFFIX:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if PUBLIC_ALLOW in line:
                    continue
                if rx.search(nfc(line)):
                    hits.append(f"{f}:{i}: {name}")
    return hits


# ── Каждый уходящий коммит (№541) ────────────────────────────────────────────

_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_HUNK = re.compile(r"^(@+) .*?\+(\d+)(?:,\d+)? @+")


_ESCAPES = {b"n": b"\n", b"t": b"\t", b'"': b'"', b"\\": b"\\", b"a": b"\a", b"b": b"\b",
            b"f": b"\f", b"r": b"\r", b"v": b"\v"}


def _unescape(m: re.Match[bytes]) -> bytes:
    code = m.group(1)
    return bytes([int(code, 8)]) if code[:1].isdigit() else _ESCAPES[code]


def _unquote_path(raw: str) -> str:
    """Путь из заголовка `+++ b/…`: git берёт в C-кавычки имена со спецсимволами
    (кавычки всегда парные); хвост `\t` git ставит после имени с пробелом."""
    raw = raw.rstrip("\t")
    if not raw.startswith('"'):
        return raw
    body = re.sub(rb"\\([0-7]{3}|.)", _unescape, raw[1:-1].encode("utf-8"), flags=re.S)
    return body.decode("utf-8", "replace")


def added_lines(revs: list[str]) -> Iterator[tuple[str, str, int, str]]:
    """Добавленные строки КАЖДОГО коммита набора: (коммит, путь, номер, текст).

    Слияние — комбинированным диффом (`--cc`): добавленной считается строка,
    которой нет ни в одном родителе (все N колонок префикса — `+`); строки
    родителей проверены их собственными коммитами или уже опубликованы.
    Двоичные по мнению git файлы судит `blob_texts` — по содержимому блоба, а не
    по диффу. Pathspec нет намеренно: с ним git упрощает историю, и слияние,
    равное родителю, прятало коммиты влитой ветки (выход 1 №541, Sonnet C1).
    Содержимое медиа-суффиксов отсекается по пути в разборе, их имена проверяет
    `added_paths`.
    """
    out = git("log", "--format=%x01%H", "-p", "-U0", "--cc", "--no-renames",
              "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/",
              "--no-show-signature", *revs, "--")
    return parse_patch(out.decode("utf-8", "replace"), None)


def parse_patch(text: str, sha: str | None) -> Iterator[tuple[str, str, int, str]]:
    """Автомат разбора патча: `\x01<sha>` — коммит; `diff ` — заголовок файла
    (только в нём читается `+++ b/…`); `@@`/`@@@` — хунк, в нём `+++` —
    обычная добавленная строка. Непонятный заголовок файла — отказ, а не
    «нечего судить» (финальный круг №541, Opus M5)."""
    path = None
    mode = ""
    parents: int      # заданы заголовком хунка до первого чтения
    lineno: int
    for line in text.split("\n"):
        if line.startswith("\x01"):
            if not _SHA.fullmatch(line[1:]):
                raise GitError(f"разбор журнала: не коммит — {_clip(line[1:])!r}")
            sha, path, mode = line[1:], None, ""
            continue
        if line.startswith("diff "):
            path, mode = None, "header"
            continue
        if mode == "header":
            if line.startswith("+++ "):
                rest = _unquote_path(line[4:])
                if rest.startswith("b/"):
                    path = rest[2:]
                elif rest != "/dev/null":                 # /dev/null — файл удалён
                    raise GitError(f"разбор патча: заголовок {_clip(rest)!r}")
            m = _HUNK.match(line)
            if m:
                parents, lineno, mode = len(m.group(1)) - 1, int(m.group(2)), "hunk"
            continue
        if mode == "hunk":
            m = _HUNK.match(line)
            if m:
                parents, lineno = len(m.group(1)) - 1, int(m.group(2))
                continue
            prefix = line[:parents]
            if len(prefix) < parents or line.startswith("\\"):
                continue
            if prefix == "+" * parents:
                if sha and path is not None and \
                        pathlib.PurePosixPath(path).suffix.lower() not in SKIP_SUFFIX:
                    yield sha, path, lineno, line[parents:]
                lineno += 1
            elif "-" not in prefix:
                lineno += 1          # строка результата, взятая из родителя


def _z_records(out: bytes) -> Iterator[tuple[str, list[str]]]:
    """Вывод `git log -z --format=%x01%H …` → (коммит, его поля).

    Режется сначала по NUL, коммит — поле `\x01<sha>`: имя файла может
    содержать и `\x01`, и перевод строки, но не NUL."""
    sha, fields = None, []
    for token in out.decode("utf-8", "replace").split("\0"):
        token = token.lstrip("\n")
        if token.startswith("\x01") and _SHA.fullmatch(token[1:]):
            if sha:
                yield sha, fields
            sha, fields = token[1:], []
        elif token:
            if sha is None:
                raise GitError(f"разбор журнала: поле до коммита — {_clip(token)!r}")
            fields.append(token)
    if sha:
        yield sha, fields


def added_paths(revs: list[str]) -> list[tuple[str, str]]:
    """Имена файлов, которые коммит добавляет или меняет — включая двоичные и
    пустые, у которых в патче нет `+++` (круг 2 №541, Sonnet C1)."""
    out = git("log", "-z", "--raw", "--no-renames", "--no-abbrev",
              "--diff-merges=first-parent", "--format=%x01%H", *revs, "--")
    found: list[tuple[str, str]] = []
    for sha, fields in _z_records(out):
        for meta, name in zip(fields[0::2], fields[1::2]):
            if not meta.startswith(":"):
                raise GitError(f"разбор путей: неожиданная запись {_clip(meta)!r}")
            if not meta.split()[-1].startswith("D"):
                found.append((sha, name))
    return found


@dataclass
class CommitMeta:
    sha: str
    author: str
    author_email: str
    committer: str
    committer_email: str
    message: str


def commit_meta(revs: list[str]) -> list[CommitMeta]:
    out = git("log", "-z", "--encoding=UTF-8", "--no-show-signature",
              "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B", *revs, "--")
    tokens = out.decode("utf-8", "replace").split("\0")
    if tokens[-1] == "":           # split всегда даёт хотя бы один элемент
        tokens.pop()
    if len(tokens) % 6:
        raise GitError("разбор сообщений коммитов: поля не сошлись")
    metas = []
    for i in range(0, len(tokens), 6):
        sha = tokens[i].strip()
        if not _SHA.fullmatch(sha):
            raise GitError(f"разбор сообщений коммитов: не коммит — {_clip(sha)!r}")
        metas.append(CommitMeta(sha, *tokens[i + 1:i + 6]))
    return metas


# Двоичный файл (по мнению git: NUL в начале) судится по содержимому блоба —
# печатные отрезки от 8 символов; больше потолка — отказ, а не тихий пропуск.
BLOB_LIMIT = 20 * 1024 * 1024
_PRINTABLE_RUN = re.compile(r"[^\x00-\x08\x0b-\x1f\x7f\ufffd]{8,}")


def binary_files(revs: list[str]) -> list[tuple[str, str]]:
    """(коммит, путь) двоичных файлов, которые коммит добавляет или меняет."""
    out = git("log", "-z", "--numstat", "--no-renames", "--diff-merges=first-parent",
              "--format=%x01%H", *revs, "--")
    found: list[tuple[str, str]] = []
    for sha, fields in _z_records(out):
        for entry in fields:
            parts = entry.split("\t", 2)
            if parts[:2] == ["-", "-"] and len(parts) == 3:
                found.append((sha, parts[2]))
    return found


# Сжатые контейнеры, которые страж не распаковывает: такой файл — отказ, а не
# «чисто» (финальный круг №541, Opus I1). zip (в том числе .docx/.xlsx) и gzip
# распаковываются.
_UNREADABLE = {b"7z\xbc\xaf\x27\x1c": "7z", b"\xfd7zXZ\x00": "xz", b"BZh": "bzip2",
               b"Rar!\x1a\x07": "rar", b"\x28\xb5\x2f\xfd": "zstd"}
_TAG = re.compile(rb"<[^<>]{0,2000}>")


def _utf16(data: bytes) -> str | None:
    """Текст в UTF-16 (с BOM или без): у латиницы старший байт 0, у кириллицы 4 —
    печатных отрезков в нём нет, судить надо декодированный текст."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    head = data[:4096]
    for enc, high in (("utf-16-le", head[1::2]), ("utf-16-be", head[0::2])):
        if len(high) > 8 and sum(b <= 4 for b in high) > len(high) * 0.9:
            return data.decode(enc, "replace")
    return None


def blob_texts(data: bytes, depth: int = 0) -> list[str]:
    """Текстовые представления двоичного блоба; непроверяемое — отказ."""
    if depth > 3:
        raise GitError("вложенность контейнеров глубже 3 — содержимое не проверить")
    if data.startswith(b"PK\x03\x04"):
        texts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.file_size > BLOB_LIMIT:
                    raise GitError(f"член архива {info.filename} больше потолка {BLOB_LIMIT} Б")
                member = z.read(info)
                if info.filename.endswith((".xml", ".rels")):
                    member = _TAG.sub(b"", member)   # слово в .docx бывает разрезано тегами
                texts += blob_texts(member, depth + 1) + [info.filename]
        return texts
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            inner = g.read(BLOB_LIMIT + 1)
        if len(inner) > BLOB_LIMIT:
            raise GitError(f"распакованный gzip больше потолка {BLOB_LIMIT} Б")
        return blob_texts(inner, depth + 1)
    for sig, kind in _UNREADABLE.items():
        if data.startswith(sig):
            raise GitError(f"сжатый контейнер {kind} — содержимое не проверить")
    wide = _utf16(data)
    if wide is not None:
        return [wide]
    return _PRINTABLE_RUN.findall(data.decode("utf-8", "replace"))


def binary_texts(sha: str, path: str) -> list[str] | None:
    """Текст двоичного файла коммита; None — медиа-суффикс (судится только имя)."""
    if pathlib.PurePosixPath(path).suffix.lower() in SKIP_SUFFIX:
        return None
    obj = f"{sha}:{path}"
    if not git_ok("cat-file", "-e", obj):
        raise GitError("объект файла не найден — имя не разобрать, содержимое не проверить")
    size = int(git("cat-file", "-s", obj).strip() or 0)
    if size > BLOB_LIMIT:
        raise GitError(f"двоичный файл {size} Б больше потолка {BLOB_LIMIT} Б — содержимое не "
                       "проверить, такой файл не должен уходить в публичный репозиторий без решения")
    return blob_texts(git("cat-file", "blob", obj))


def raw_objects(shas: list[str]) -> dict[str, str]:
    """Сырой текст объектов одним `cat-file --batch`."""
    out = git("cat-file", "--batch", stdin=("\n".join(shas) + "\n").encode())
    objs: dict[str, str] = {}
    pos = 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        head = out[pos:end].split()
        if len(head) != 3:
            raise GitError(f"cat-file --batch: {_clip(out[pos:end].decode('utf-8', 'replace'))!r}")
        size = int(head[2])
        objs[head[0].decode()] = out[end + 1:end + 1 + size].decode("utf-8", "replace")
        pos = end + 1 + size + 1
    return objs


# Заголовки коммита, которые судятся отдельно (автор, коммитер) или не текст
# (дерево, родители, подпись). Остальные — mergetag и любые будущие — судятся.
_KNOWN_HEADERS = {"tree", "parent", "author", "committer", "encoding", "gpgsig", "gpgsig-sha256"}


def extra_headers(raw: str) -> list[tuple[str, str]]:
    """(имя, текст) заголовков коммита вне %B: mergetag несёт текст влитого тега."""
    found: list[tuple[str, str]] = []
    for line in raw.split("\n\n", 1)[0].split("\n"):
        if line.startswith(" ") and found:
            found[-1] = (found[-1][0], found[-1][1] + "\n" + line[1:])
        elif not line.startswith(" "):
            key, _, value = line.partition(" ")
            found.append((key, value))
    return [(k, v) for k, v in found if k not in _KNOWN_HEADERS]


def scan_commits(revs: list[str], private: re.Pattern[str] | None, identity: bool,
                 extra: list[tuple[str, str]] = (),
                 extra_emails: list[tuple[str, str]] = ()) -> tuple[int, list[str]]:
    """Что публикует каждый коммит набора — строки, имена файлов (и содержимое
    двоичных), сообщение, имена и почты — против обоих наборов; плюс `extra`
    (имена ссылок, аннотации тегов) и почты тегеров. Возвращает (число
    коммитов, находки).

    Находка — место без цитаты: вывод уходит в журналы фоновых сессий; путь,
    в котором сработал формат или маркер, печатается с маской.
    """
    def kinds(text: str) -> list[str]:
        # По строкам: пометка PUBLIC_ALLOW гасит только свою строку сообщения.
        text = nfc(text)
        found = ["приватный маркер"] if private and private.search(text) else []
        for line in text.splitlines() or [text]:
            found += [k for k in public_hits(line) if k not in found]
        return found

    def mask(name: str) -> str:
        name = nfc(name)
        if private:
            name = private.sub("***", name)
        for raw in PUBLIC_PATTERNS.values():
            name = re.sub(raw, "***", name)
        return name

    hits: list[str] = []
    for field, text in extra:
        for k in kinds(text):
            hits.append(f"{mask(field)}: {k}")
    if identity:
        for field, email in extra_emails:
            if email != MAINTAINER_EMAIL:
                hits.append(f"{mask(field)}: почта не {MAINTAINER_EMAIL}")
    metas = commit_meta(revs) if revs else []
    for m in metas:
        short = m.sha[:9]
        for field, text in (("сообщение", m.message), ("автор", f"{m.author} <{m.author_email}>"),
                            ("коммитер", f"{m.committer} <{m.committer_email}>")):
            for k in kinds(text):
                hits.append(f"{short} {field}: {k}")
        if identity:
            for field, email in (("автор", m.author_email), ("коммитер", m.committer_email)):
                if email != MAINTAINER_EMAIL:
                    hits.append(f"{short} {field}: почта не {MAINTAINER_EMAIL}")
    if not metas:
        return 0, hits
    for sha, raw in raw_objects([m.sha for m in metas]).items():
        for key, text in extra_headers(raw):
            for k in kinds(text):
                hits.append(f"{sha[:9]} заголовок {key}: {k}")
    present = added_paths(revs)
    for sha, name in present:
        for k in kinds(name):
            hits.append(f"{sha[:9]} имя файла {mask(name)}: {k}")
    for sha, name, lineno, text in added_lines(revs):
        for k in kinds(text):
            hits.append(f"{sha[:9]} {mask(name)}:{lineno}: {k}")
    kept = set(present)
    for sha, name in binary_files(revs):
        if (sha, name) not in kept:
            continue                     # удалён: numstat показывает и удалённые
        try:
            texts = binary_texts(sha, name)
        except GitError as e:
            raise GitError(f"{sha[:9]} {mask(name)}: {e}") from None
        found: list[str] = []
        for run in texts or ():
            found += [k for k in kinds(run) if k not in found]
        for k in found:
            hits.append(f"{sha[:9]} {mask(name)} (двоичный): {k}")
    return len(metas), hits


def _zero(sha: str) -> bool:
    return set(sha) == {"0"}


def _known_remote(remote: str) -> list[str]:
    names = git("remote").decode("utf-8", "replace").split()
    if remote not in names:
        # userinfo адреса (логин, токен) в журнал не попадает
        shown = re.sub(r"//[^/@]*@", "//", remote)
        raise GitError(f"push не в названный remote ({shown!r}): опубликованное не с чем "
                       f"сравнить — пушьте в remote из `git remote` ({', '.join(names) or 'нет'})")
    return names


@dataclass
class PushSet:
    revs: list[str] | None
    notes: list[str]
    texts: list[tuple[str, str]]       # (что, текст): имена ссылок, аннотации тегов
    emails: list[tuple[str, str]]      # (что, почта): тегеры


def server_tips(remote: str) -> list[str]:
    """Вершины веток и тегов на сервере сейчас, известные локально.

    Опубликованное берётся у сервера, а не из remote-tracking ссылок: ветку,
    удалённую на сервере (например, после утечки), кэш считал бы опубликованной
    и повторный push вернул бы её мимо стража (выход 2 №541, Sonnet I3).
    """
    out = git("ls-remote", "--heads", "--tags", remote).decode("utf-8", "replace")
    shas = sorted({ln.split()[0] for ln in out.splitlines() if ln.strip()})
    if not shas:
        return []
    # batch-check отвечает «<sha> <тип> <размер>» или «<sha> missing»
    known = git("cat-file", "--batch-check", stdin="\n".join(shas).encode() + b"\n")
    return [ln.split()[0] for ln in known.decode().splitlines()
            if ln.split()[1] in ("commit", "tag")]


def push_revs(remote: str, lines: list[str], url: str | None = None) -> PushSet:
    """Набор для stdin хука pre-push — все строки, не только первая.

    `L… --not <вершины сервера>`: уже опубликованное не судится повторно;
    force-push после ребейза не тащит коммиты main. Удаление ветки (нулевой
    local sha) — пропуск. Remote sha из stdin отдельно не нужен: git берёт его
    у сервера при согласовании push, он и так среди вершин `ls-remote`. Имя
    ветки на сервере и аннотация тега тоже публикуются.

    Вершины спрашиваются по адресу, куда git реально пушит (`url` — второй
    аргумент хука: pushurl может отличаться от fetch url), и у публичного
    origin: push в пустое зеркало не судит заново опубликованную историю
    (финальный круг №541, Opus I2, M3).
    """
    names = _known_remote(remote)
    local, notes = [], []
    texts: list[tuple[str, str]] = []
    emails: list[tuple[str, str]] = []
    for raw in lines:
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 4:
            raise GitError(f"строка хука pre-push не разобрана: {_clip(raw)!r}")
        _local_ref, lsha, remote_ref, _rsha = parts
        if _zero(lsha):
            continue
        local.append(lsha)
        texts.append((f"имя ссылки {remote_ref}", remote_ref))
        obj = lsha
        while git("cat-file", "-t", obj).strip() == b"tag":     # тег на тег — вся цепочка
            body = git("cat-file", "tag", obj).decode("utf-8", "replace")
            texts.append((f"тег {remote_ref}", body))
            tagger = re.search(r"^tagger .*<([^>]*)>", body, re.M)
            emails.append((f"тег {remote_ref}", tagger.group(1) if tagger else ""))
            obj = re.search(r"^object (\S+)", body, re.M).group(1)
    if not local:
        return PushSet(None, notes, texts, emails)
    tips = server_tips(url or remote)
    if remote != "origin" and "origin" in names:
        tips += server_tips("origin")
    return PushSet([*local, "--not", *tips], notes, texts, emails)


def env_revs() -> list[str]:
    """Набор для записи pre-commit стадии pre-push (контрибьюторы).

    pre-commit отдаёт одну пару FROM/TO — только первую ветку push нескольких;
    это ограничение pre-commit, записано в CONTRIBUTING.
    """
    remote = os.environ.get("PRE_COMMIT_REMOTE_NAME", "")
    to = os.environ.get("PRE_COMMIT_TO_REF") or os.environ.get("PRE_COMMIT_LOCAL_BRANCH", "")
    if not remote or not to:
        raise GitError("нет PRE_COMMIT_REMOTE_NAME или PRE_COMMIT_TO_REF/LOCAL_BRANCH — "
                       "режим только для стадии pre-push pre-commit")
    _known_remote(remote)
    # FROM_REF у pre-commit — вершина remote или предок, опубликованный на нём:
    # `--remotes` покрывает оба случая.
    return [to, "--not", f"--remotes={remote}"]


# ── Хуки владельца ──────────────────────────────────────────────────────────

PRE_COMMIT_HOOK = """#!/bin/bash
# Локальный хук делегирует стражу из репозитория: единственный источник
# правды — scripts/check_private_markers.py, он же едет с клоном.
exec python3 "$(git rev-parse --show-toplevel)/scripts/check_private_markers.py"
"""

# Страж берётся из основного checkout на main, а не из пушимой ветки: ветка не
# может ослабить гейт, через который публикуется (круг 1 №541, обе головы).
# Правка стража действует после мержа и pull основного checkout.
PRE_PUSH_HOOK = """#!/bin/bash
# pre-push: страж обезличивания по каждому уходящему коммиту (№541).
set -u
C="$(git rev-parse --path-format=absolute --git-common-dir)/.." || exit 1
S="$C/scripts/check_private_markers.py"
canon() { GIT_OPTIONAL_LOCKS=0 env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git -C "$C" "$@"; }
if [ "$(canon symbolic-ref -q --short HEAD)" != main ]; then
  echo "pre-push: основной checkout ( $C ) не на ветке main — канон стража не определён" >&2; exit 1
fi
if ! canon diff --quiet HEAD -- scripts/check_private_markers.py; then
  echo "pre-push: в основном checkout ( $C ) незакоммиченная правка стража" >&2; exit 1
fi
if ! grep -q -- '--pre-push' "$S"; then
  echo "pre-push: страж основного checkout старый — git -C \\"$C\\" pull --ff-only" >&2; exit 1
fi
exec python3 -I "$S" --pre-push "$1" --push-url "$2"
"""
HOOKS = {"pre-commit": PRE_COMMIT_HOOK, "pre-push": PRE_PUSH_HOOK}
# По этим строкам проверка установки узнаёт хук, не сверяя текст целиком:
# ветка, правящая текст хука, не должна отказывать самой себе в коммите.
PRE_PUSH_MARKS = ('scripts/check_private_markers.py"', '"$S" --pre-push')


def hooks_dir() -> pathlib.Path:
    return pathlib.Path(git("rev-parse", "--path-format=absolute", "--git-path", "hooks")
                        .decode().strip())


def canonical_guard() -> pathlib.Path:
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    return pathlib.Path(common).parent / "scripts" / "check_private_markers.py"


def pre_push_problem() -> str | None:
    """Почему push уйдёт мимо стража; None — хук на месте или ставить его рано.

    Рано — пока страж основного checkout не знает `--pre-push` (до мержа и pull):
    такой хук отказал бы каждому push. После pull проверка включается сама.
    """
    try:
        canon = canonical_guard().read_text(encoding="utf-8", errors="replace")
    except OSError:
        canon = ""
    if "--pre-push" not in canon:
        return None
    hook = hooks_dir() / "pre-push"
    if not hook.is_file():
        return f"нет хука pre-push ({hook})"
    if not os.access(hook, os.X_OK):
        return f"хук pre-push не исполняемый ({hook})"
    text = hook.read_text(encoding="utf-8", errors="replace")
    legacy = hook.with_name("pre-push.legacy")
    if "hook-impl" in text and legacy.is_file():
        # `pre-commit install` переносит чужой хук в .legacy и зовёт его сам
        # (выход 2 №541, Sonnet I2): судим то, что будет исполнено.
        text = legacy.read_text(encoding="utf-8", errors="replace")
        if not os.access(legacy, os.X_OK):
            return f"хук pre-push.legacy не исполняемый ({legacy})"
    if not all(m in text for m in PRE_PUSH_MARKS):
        return f"хук pre-push не зовёт страж ({hook})"
    return None


def install_hooks(force: bool) -> int:
    top = pathlib.Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    common = pathlib.Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")
                          .decode().strip())
    branch = subprocess.run([*_GIT, "symbolic-ref", "-q", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    if top != common.parent.resolve() or branch != "main":
        print(f"❌ хуки ставятся из основного checkout на main ({common.parent}), "
              f"а здесь {top} на {branch or 'detached HEAD'}", file=sys.stderr)
        return 1
    hooks_path = subprocess.run([*_GIT, "config", "core.hooksPath"],
                                capture_output=True).stdout.decode("utf-8", "replace").strip()
    if hooks_path:
        print(f"❌ задан core.hooksPath ({hooks_path}): хуки легли бы туда, возможно во все "
              "репозитории машины — снимите его или поставьте хуки руками", file=sys.stderr)
        return 1
    target = hooks_dir()
    target.mkdir(exist_ok=True)
    rc = 0
    for name, text in HOOKS.items():
        path = target / name
        if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
            print(f"{name}: уже стоит")
            continue
        if path.exists() and not force:
            print(f"❌ {path} отличается от текста стража — не затираю (--force заменит):",
                  file=sys.stderr)
            print(path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            rc = 1
            continue
        if path.exists():
            path.with_name(f"{name}.bak").write_bytes(path.read_bytes())
            print(f"{name}: прежний сохранён в {name}.bak")
        tmp = path.with_name(f".{name}.tmp{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o755)
        os.replace(tmp, path)
        print(f"{name}: поставлен ({path})")
    return rc


def _report(title: str, hits: list[str]) -> None:
    print(f"❌ {title}: {len(hits)}", file=sys.stderr)
    for h in hits[:50]:
        print(f"  {h}", file=sys.stderr)
    if len(hits) > 50:
        print(f"  … ещё {len(hits) - 50}", file=sys.stderr)


def owner_markers_path() -> pathlib.Path:
    """Режимы владельца: на машине владельца — только список по умолчанию;
    CHAROITE_MARKERS его не подменяет (выход 2 №541, Sonnet M1)."""
    default = default_markers_path()
    return default if default.exists() else markers_path()


def run_commits(revs: list[str] | None, need_list: bool, identity: bool,
                notes: list[str], extra: list[tuple[str, str]] = (),
                extra_emails: list[tuple[str, str]] = ()) -> int:
    """Общий ход трёх режимов по коммитам. `need_list` — нет списка маркеров
    значит отказ, и `CI` этого не меняет: режимы владельца в CI не зовутся, а
    переменная из окружения сессии не должна выключать гейт (выход 1 №541,
    Sonnet I1). Без `need_list` (контрибьютор) — только публичные форматы."""
    for n in notes:
        print(f"  {n}")
    path = owner_markers_path() if need_list else markers_path()
    private = None
    if path.exists():
        markers = load_markers(path)
        if not markers:
            print("❌ список маркеров пуст — fail-closed", file=sys.stderr)
            return 1
        private = build_pattern(markers)
    if revs is None:
        print("нечего проверять: push только удаляет ветки")
        return 0
    count, hits = scan_commits(revs, private, identity, extra, extra_emails)
    missing = private is None and need_list
    if hits:
        _report("PUSH ЗАБЛОКИРОВАН — уходящие коммиты публикуют приватное", hits)
        print("Обезличь и перепиши эти коммиты (rebase), не поверх: промежуточный "
              "коммит публикуется вместе с исправлением.", file=sys.stderr)
    if missing:
        print(f"❌ список маркеров отсутствует ({path}) — fail-closed; "
              "публичные форматы проверены", file=sys.stderr)
    if hits or missing:
        return 1
    what = "оба набора" if private else "публичные форматы (списка маркеров нет)"
    print(f"коммитов проверено: {count}, {what}: чисто")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true",
                      help="всё дерево, а не только добавленные строки дифа коммита")
    mode.add_argument("--public-only", action="store_true",
                      help="режим CI: только публичные шаблоны, приватного списка там нет")
    mode.add_argument("--range", metavar="РЕВИЗИИ",
                      help="каждый коммит диапазона (например BASE..HEAD), оба набора")
    mode.add_argument("--pre-push", metavar="REMOTE",
                      help="хук pre-push владельца: stdin git, все строки; плюс почта коммитов")
    ap.add_argument("--push-url", help="с --pre-push: адрес, куда git пушит (второй аргумент хука)")
    mode.add_argument("--range-from-env", action="store_true",
                      help="стадия pre-push pre-commit (контрибьюторы): PRE_COMMIT_* из окружения")
    mode.add_argument("--install-hooks", action="store_true",
                      help="поставить pre-commit и pre-push в общий каталог хуков клона")
    ap.add_argument("--force", action="store_true", help="с --install-hooks: заменить отличающиеся")
    a = ap.parse_args()
    try:
        return _run(a)
    except GitError as e:
        print(f"❌ страж не смог проверить — отказ: {e}", file=sys.stderr)
        return 1


def _run(a: argparse.Namespace) -> int:
    if a.install_hooks:
        return install_hooks(a.force)
    # `is not None`: пустой аргумент — не повод молча уйти в режим pre-commit.
    for flag, value in (("--range", a.range), ("--pre-push", a.pre_push)):
        if value is not None and not value.strip():
            print(f"❌ {flag} с пустым значением — проверять нечего, отказ", file=sys.stderr)
            return 1
    if a.range is not None:
        return run_commits(a.range.split(), need_list=True, identity=False, notes=[])
    if a.pre_push is not None:
        push = push_revs(a.pre_push, sys.stdin.read().splitlines(), a.push_url)
        return run_commits(push.revs, need_list=True, identity=True, notes=push.notes,
                           extra=push.texts, extra_emails=push.emails)
    if a.range_from_env:
        return run_commits(env_revs(), need_list=False, identity=False, notes=[])
    full_only = a.all
    # Режим CI: только публичные шаблоны, приватного списка там нет.
    if a.public_only:
        hits = scan_public(tracked_files())
        if hits:
            print("❌ похоже на приватные данные в публичном дереве:", file=sys.stderr)
            for h in hits:
                print(f"  {h}", file=sys.stderr)
            print("Это проверка ФОРМАТОВ. Имена и внутренние названия ловит "
                  "локальный хук — он остаётся главным рубежом.", file=sys.stderr)
            return 1
        print("публичные шаблоны: чисто")
        return 0
    path = markers_path()
    if not path.exists():
        # fail-closed на машине автора и мягкий пропуск в CI и у контрибьюторов:
        # приватного списка у них нет и быть не должно.
        if os.environ.get("CI"):
            print("список маркеров недоступен в CI — проверка пропущена")
            return 0
        print("❌ список маркеров отсутствует — fail-closed", file=sys.stderr)
        return 1

    markers = load_markers(path)
    if not markers:
        print("❌ список маркеров пуст — fail-closed", file=sys.stderr)
        return 1
    pattern = build_pattern(markers)

    # Полный проход по дереву. Диф-проверка ловит то, что пишут сейчас, а это —
    # то, что уже опубликовано: маркер, попавший в main до пополнения списка,
    # иначе не всплывёт никогда. Стоит миллисекунды на двух сотнях файлов.
    stale = scan_files(pattern, tracked_files())
    if stale:
        print(f"❌ приватные маркеры в опубликованном дереве: {len(stale)}",
              file=sys.stderr)
        for place in stale[:10]:
            print(f"  {place}", file=sys.stderr)
        print("Обезличь эти строки. Полный список мест: "
              "python3 scripts/check_private_markers.py --all", file=sys.stderr)
        return 1
    if full_only:
        print(f"дерево чисто: {len(tracked_files())} файлов, маркеров нет")
        return 0

    diff = git("diff", "--cached", "-U0", "--no-renames", "--no-ext-diff", "--no-textconv",
               "--src-prefix=a/", "--dst-prefix=b/").decode("utf-8", "replace")
    # Место, а не цитата: коммитят и фоновые сессии, их вывод — журнал (Opus M2).
    hits = [f"{name}:{lineno}" for _sha, name, lineno, text in parse_patch(diff, "индекс")
            if pattern.search(nfc(text))]

    if hits:
        print(f"❌ КОММИТ ЗАБЛОКИРОВАН: {len(hits)} строк с личными/банковскими маркерами:",
              file=sys.stderr)
        for place in hits[:5]:
            print(f"  {place}", file=sys.stderr)
        print(f"Обезличь (имена/системы/пути) и повтори. Список: {path}", file=sys.stderr)
        return 1

    author = subprocess.run(["git", "config", "user.email"],
                            capture_output=True, text=True).stdout.strip()
    if not os.environ.get("CI") and author != MAINTAINER_EMAIL:
        print(f"❌ Автор коммита {author} ≠ {MAINTAINER_EMAIL} (публичный репо!)",
              file=sys.stderr)
        return 1

    # Проверка установки pre-push — на машине владельца, то есть там, где лежит
    # список по умолчанию; ни CHAROITE_MARKERS, ни CI её не выключают (тесты
    # изолируют HOME). Push без хука публикует мимо стража молча; коммит —
    # место, где владелец точно проходит (№541).
    if default_markers_path().exists():
        problem = pre_push_problem()
        if problem:
            print(f"❌ {problem}: push уйдёт мимо стража. В основном checkout на main: "
                  "python3 scripts/check_private_markers.py --install-hooks", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
