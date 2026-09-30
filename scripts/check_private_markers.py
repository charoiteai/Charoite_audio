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
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass

# До этой длины маркер считается аббревиатурой и ищется по границам слова.
SHORT_MARKER = 4
# Публичный репозиторий коммитится под одним именем.
MAINTAINER_EMAIL = "charoiteai@gmail.com"


def markers_path() -> pathlib.Path:
    env = os.environ.get("CHAROITE_MARKERS")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".config" / "charoite" / "private_markers.txt"


def load_markers(path: pathlib.Path) -> list[str]:
    """Единственный читатель списка: пустые строки и `#`-комментарии — не маркеры."""
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
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
_GIT = ("git", "-c", "core.quotepath=false", "-c", "color.ui=never",
        "-c", "log.showRoot=true")


def git(*args: str) -> bytes:
    p = subprocess.run([*_GIT, *args], capture_output=True)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args[:2])}: {err[:300] or f'код {p.returncode}'}")
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
            if pattern.search(line):
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
    """Какие публичные форматы сработали на строке (пометка — пропуск)."""
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
                if rx.search(line):
                    hits.append(f"{f}:{i}: {name}")
    return hits


# ── Каждый уходящий коммит (№541) ────────────────────────────────────────────

_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_HUNK = re.compile(r"^(@+) .*?\+(\d+)(?:,\d+)? @+")
# Содержимое медиа не читается (двоичное, большое); их ИМЕНА проверяются.
_MEDIA_EXCLUDE = tuple(f":(exclude,glob,icase)**/*{s}" for s in sorted(SKIP_SUFFIX))


def _unquote_path(raw: str) -> str:
    """Путь из заголовка `+++ b/…`: git берёт в C-кавычки имена со спецсимволами."""
    raw = raw.rstrip("\t")
    if not (raw.startswith('"') and raw.endswith('"')):
        return raw
    body = raw[1:-1].encode("utf-8")
    out = bytearray()
    i = 0
    simple = {ord("n"): 10, ord("t"): 9, ord('"'): 34, ord("\\"): 92, ord("a"): 7,
              ord("b"): 8, ord("f"): 12, ord("r"): 13, ord("v"): 11}
    while i < len(body):
        c = body[i]
        if c == 92 and i + 1 < len(body):
            n = body[i + 1]
            if n in simple:
                out.append(simple[n])
                i += 2
                continue
            if 48 <= n <= 55 and i + 4 <= len(body):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
        out.append(c)
        i += 1
    return out.decode("utf-8", "replace")


def added_lines(revs: list[str]) -> Iterator[tuple[str, str, int, str]]:
    """Добавленные строки КАЖДОГО коммита набора: (коммит, путь, номер, текст).

    Слияние — комбинированным диффом (`--cc`): добавленной считается строка,
    которой нет ни в одном родителе (все N колонок префикса — `+`); строки
    родителей проверены их собственными коммитами или уже опубликованы.
    Разбор — автомат: `+++ b/…` читается только в заголовке файла, в хунке это
    обычная добавленная строка.
    """
    out = git("log", "--format=%x01%H", "-p", "-U0", "--cc", "--text", "--no-renames",
              "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/",
              "--no-show-signature", *revs, "--", ".", *_MEDIA_EXCLUDE)
    sha = path = None
    mode = ""
    parents = 1
    lineno = 0
    for line in out.decode("utf-8", "replace").split("\n"):
        if line.startswith("\x01"):
            if not _SHA.fullmatch(line[1:]):
                raise GitError(f"разбор журнала: не коммит — {line[1:60]!r}")
            sha, path, mode = line[1:], None, ""
            continue
        if line.startswith("diff "):
            path, mode = None, "header"
            continue
        if mode == "header":
            if line.startswith("+++ "):
                rest = _unquote_path(line[4:])
                path = rest[2:] if rest.startswith("b/") else None   # /dev/null — удалён
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
                if sha and path is not None:
                    yield sha, path, lineno, line[parents:]
                lineno += 1
            elif "-" not in prefix:
                lineno += 1          # строка результата, взятая из родителя


def added_paths(revs: list[str]) -> list[tuple[str, str]]:
    """Имена файлов, которые коммит добавляет или меняет — включая двоичные и
    пустые, у которых в патче нет `+++` (круг 2 №541, Sonnet C1)."""
    out = git("log", "-z", "--raw", "--no-renames", "--no-abbrev",
              "--diff-merges=first-parent", "--format=%x01%H", *revs, "--")
    found: list[tuple[str, str]] = []
    for chunk in out.decode("utf-8", "replace").split("\x01")[1:]:
        tokens = chunk.split("\0")
        sha = tokens[0].strip()
        if not _SHA.fullmatch(sha):
            raise GitError(f"разбор путей: не коммит — {sha[:60]!r}")
        rest = tokens[1:]
        for meta, name in zip(rest[0::2], rest[1::2]):
            meta = meta.strip()
            if not meta.startswith(":"):
                raise GitError(f"разбор путей: неожиданная запись {meta[:60]!r}")
            if not meta.split()[-1].startswith("D"):
                found.append((sha, name))
    return found


@dataclass(frozen=True)
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
    if tokens and tokens[-1] == "":
        tokens.pop()
    if len(tokens) % 6:
        raise GitError("разбор сообщений коммитов: поля не сошлись")
    metas = []
    for i in range(0, len(tokens), 6):
        sha = tokens[i].strip()
        if not _SHA.fullmatch(sha):
            raise GitError(f"разбор сообщений коммитов: не коммит — {sha[:60]!r}")
        metas.append(CommitMeta(sha, *tokens[i + 1:i + 6]))
    return metas


def scan_commits(revs: list[str], private: re.Pattern[str] | None,
                 identity: bool) -> tuple[int, list[str]]:
    """Что публикует каждый коммит набора — строки, имена файлов, сообщение,
    имена и почты — против обоих наборов. Возвращает (число коммитов, находки).

    Находка — место без цитаты: вывод уходит в журналы фоновых сессий.
    """
    def kinds(text: str) -> list[str]:
        found = ["приватный маркер"] if private and private.search(text) else []
        return found + public_hits(text)

    metas = commit_meta(revs)
    hits: list[str] = []
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
    for sha, name in added_paths(revs):
        for k in kinds(name):
            hits.append(f"{sha[:9]} имя файла {name}: {k}")
    for sha, name, lineno, text in added_lines(revs):
        for k in kinds(text):
            hits.append(f"{sha[:9]} {name}:{lineno}: {k}")
    return len(metas), hits


def _zero(sha: str) -> bool:
    return set(sha) == {"0"}


def _known_remote(remote: str) -> None:
    names = git("remote").decode("utf-8", "replace").split()
    if remote not in names:
        raise GitError(f"push не в названный remote ({remote!r}): опубликованное не с чем "
                       f"сравнить — пушьте в remote из `git remote` ({', '.join(names) or 'нет'})")


def push_revs(remote: str, lines: list[str]) -> tuple[list[str] | None, list[str]]:
    """Набор ревизий для stdin хука pre-push — все строки, не только первая.

    `L… --not --remotes=<remote> R…`: уже опубликованное (любая ветка remote,
    прежние вершины обновляемых веток) не судится повторно; force-push после
    ребейза не тащит коммиты main. Удаление ветки (нулевой local sha) — пропуск;
    нулевой или неизвестный локально remote sha просто не исключается.
    """
    _known_remote(remote)
    local, published, notes = [], [], []
    for raw in lines:
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 4:
            raise GitError(f"строка хука pre-push не разобрана: {raw[:120]!r}")
        local_ref, lsha, _remote_ref, rsha = parts
        if _zero(lsha):
            continue
        local.append(lsha)
        if git("cat-file", "-t", lsha).strip() == b"tag":
            notes.append(f"{local_ref}: аннотация тега не проверяется, проверяются коммиты под ним")
        if not _zero(rsha) and git_ok("cat-file", "-e", f"{rsha}^{{commit}}"):
            published.append(rsha)
    if not local:
        return None, notes
    return [*local, "--not", f"--remotes={remote}", *published], notes


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
    revs = [to, "--not", f"--remotes={remote}"]
    frm = os.environ.get("PRE_COMMIT_FROM_REF", "")
    if frm and not _zero(frm) and git_ok("cat-file", "-e", f"{frm}^{{commit}}"):
        revs.append(frm)
    return revs


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
canon() { env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git -C "$C" "$@"; }
if [ "$(canon symbolic-ref -q --short HEAD)" != main ]; then
  echo "pre-push: основной checkout ( $C ) не на ветке main — канон стража не определён" >&2; exit 1
fi
if ! canon diff --quiet HEAD -- scripts/check_private_markers.py; then
  echo "pre-push: в основном checkout ( $C ) незакоммиченная правка стража" >&2; exit 1
fi
if ! grep -q -- '--pre-push' "$S"; then
  echo "pre-push: страж основного checkout старый — git -C \\"$C\\" pull --ff-only" >&2; exit 1
fi
exec python3 "$S" --pre-push "$1"
"""
HOOKS = {"pre-commit": PRE_COMMIT_HOOK, "pre-push": PRE_PUSH_HOOK}
# По этим строкам проверка установки узнаёт хук, не сверяя текст целиком:
# ветка, правящая текст хука, не должна отказывать самой себе в коммите.
PRE_PUSH_MARKS = ('scripts/check_private_markers.py"', '"$S" --pre-push')


def hooks_dir() -> pathlib.Path:
    return pathlib.Path(git("rev-parse", "--path-format=absolute", "--git-path", "hooks")
                        .decode().strip())


def pre_push_problem() -> str | None:
    hook = hooks_dir() / "pre-push"
    if not hook.is_file():
        return f"нет хука pre-push ({hook})"
    if not os.access(hook, os.X_OK):
        return f"хук pre-push не исполняемый ({hook})"
    text = hook.read_text(encoding="utf-8", errors="replace")
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
    target = hooks_dir()
    target.mkdir(parents=True, exist_ok=True)
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


def run_commits(revs: list[str] | None, need_list: bool, identity: bool,
                notes: list[str]) -> int:
    """Общий ход трёх режимов по коммитам. `need_list` — нет списка маркеров вне
    CI значит отказ (владелец); иначе без списка — только публичные форматы."""
    for n in notes:
        print(f"  {n}")
    path = markers_path()
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
    count, hits = scan_commits(revs, private, identity)
    missing = private is None and need_list and not os.environ.get("CI")
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
    if a.range:
        return run_commits(a.range.split(), need_list=True, identity=False, notes=[])
    if a.pre_push:
        revs, notes = push_revs(a.pre_push, sys.stdin.read().splitlines())
        return run_commits(revs, need_list=True, identity=True, notes=notes)
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

    diff = git("diff", "--cached", "-U0").decode("utf-8", "replace")
    added = [ln for ln in diff.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    hits = [ln for ln in added if pattern.search(ln)]

    if hits:
        print(f"❌ КОММИТ ЗАБЛОКИРОВАН: {len(hits)} строк с личными/банковскими маркерами:",
              file=sys.stderr)
        for ln in hits[:5]:
            print(f"  {ln[:160]}", file=sys.stderr)
        print(f"Обезличь (имена/системы/пути) и повтори. Список: {path}", file=sys.stderr)
        return 1

    author = subprocess.run(["git", "config", "user.email"],
                            capture_output=True, text=True).stdout.strip()
    if not os.environ.get("CI") and author != MAINTAINER_EMAIL:
        print(f"❌ Автор коммита {author} ≠ {MAINTAINER_EMAIL} (публичный репо!)",
              file=sys.stderr)
        return 1

    # Проверка установки pre-push — только на машине владельца со списком по
    # умолчанию: тесты с подставным списком её не будят, а CI списка не имеет.
    # Push без хука публикует мимо стража молча; коммит — место, где владелец
    # точно проходит (№541).
    if not os.environ.get("CHAROITE_MARKERS"):
        problem = pre_push_problem()
        if problem:
            print(f"❌ {problem}: push уйдёт мимо стража. В основном checkout на main: "
                  "python3 scripts/check_private_markers.py --install-hooks", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
