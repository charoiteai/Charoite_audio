"""Резолв [[ссылок]] графа — одна логика для doctor, облачной ревизии и уборок.

До аудита памяти 07.09 живость ссылки понимали три места по-разному:
`graph_doctor` знал путь, стем и вложение на диске, но не `aliases:` из
шапки узла (Obsidian по псевдониму резолвит — doctor считал такие ссылки
битыми, часть из 1316 «битых» была ложной); конвейер (`find_canonical`)
псевдонимы знал; облачная ревизия L4 при переносе правок из песочницы не
проверяла цели ссылок вовсе — модель писала `[[Понятие]]` без узла, и
заметка встречи ложилась в граф с битой ссылкой (GLM Critical 1). Здесь —
один резолвер и одна операция «ссылка без узла → текст», которой
пользуются и линт, и перенос облака.
"""
from __future__ import annotations

import itertools
import pathlib
import re
import unicodedata

import frontmatter
import redirects
from graph_names import name_key

# цель до `#раздел`/`|алиас` — так считает ссылки doctor
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
# для перезаписи: вложение, цель, якорь, алиас (в таблицах — `\|`)
FULL_LINK = re.compile(r"(!?)\[\[([^\]|#^]+)([#^][^\]|]*)?(?:\\?\|([^\]]*))?\]\]")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
# Папки, которые конвейер после архивации не перечитывает: битые ссылки
# там — лежащее легаси, а не гниение живой памяти (критика GLM 07.09).
ARCHIVE_DIRS = ("Встречи-архив",)


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().casefold()


def is_archive(rel: str) -> bool:
    """Заметка лежит в архивной папке (путь от корня графа)."""
    return rel.replace("\\", "/").split("/", 1)[0] in ARCHIVE_DIRS


def read_notes(root: pathlib.Path) -> dict[pathlib.Path, str]:
    """Все заметки графа: путь → текст; скрытые каталоги (.obsidian, .trash,
    снимки) и нечитаемые файлы пропускаются."""
    notes: dict[pathlib.Path, str] = {}
    for p in root.rglob("*.md"):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        try:
            notes[p] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return notes


def disk_candidates(root: pathlib.Path, target: str) -> list[pathlib.Path]:
    """Пути вложения во всех сочетаниях форм Unicode по компонентам: каталог
    может лежать в NFC, а файл в нём — в NFD (инструменты macOS пишут NFD,
    ссылки набирают в NFC; APFS это скрывает, Linux нет — luna по #450).
    Глубже трёх компонентов — только целиком NFC и NFD."""
    parts = pathlib.PurePosixPath(target).parts
    if len(parts) > 3:
        return [root / unicodedata.normalize(form, target) for form in ("NFC", "NFD")]
    out: list[pathlib.Path] = []
    for forms in itertools.product(("NFC", "NFD"), repeat=len(parts)):
        cand = root.joinpath(*(unicodedata.normalize(f, x) for f, x in zip(forms, parts)))
        if cand not in out:
            out.append(cand)
    return out


class LinkResolver:
    """Цель `[[…]]` → файл графа, как её нашёл бы Obsidian.

    Порядок: полный путь от корня → заметка с таким именем в любой папке →
    псевдоним из `aliases:` (без папки в цели или в той же папке, что узел;
    заглушки-редиректы псевдонимов не отдают — как `_alias_index`
    конвейера) → файл-вложение на диске в обеих формах Unicode.
    """

    def __init__(self, root: pathlib.Path, notes: dict[pathlib.Path, str] | None = None):
        self.root = root
        self.by_path: dict[str, pathlib.Path] = {}
        self.by_stem: dict[str, list[pathlib.Path]] = {}
        self.by_alias: dict[str, list[pathlib.Path]] = {}
        for p, text in (notes if notes is not None else read_notes(root)).items():
            self.add(p, text)

    def add(self, p: pathlib.Path, text: str) -> None:
        """Учесть заметку (новую или переписанную): путь, стем, псевдонимы."""
        rel = p.relative_to(self.root).as_posix()
        self.by_path[norm(rel[:-3] if rel.casefold().endswith(".md") else rel)] = p
        stems = self.by_stem.setdefault(norm(p.stem), [])
        if p not in stems:
            stems.append(p)
        if redirects.is_merged(text):
            return
        for alias in frontmatter.aliases(text, p.name):
            k = name_key(alias)
            if not k:
                continue
            lst = self.by_alias.setdefault(k, [])
            if p not in lst:
                lst.append(p)

    def resolve(self, target: str) -> pathlib.Path | None:
        t = target.strip().rstrip("\\").strip()      # `[[Цель\|Текст]]` в таблицах
        if not t or "\n" in t or re.search(r"\s/|/\s", t):
            return None                              # «Системы/ Витрина» — мертва (GLM I2)
        # Ссылки Obsidian считаются от корня хранилища: абсолютный путь,
        # выход через `..` и скрытые каталоги — не цели (luna, GLM по #449).
        parts = pathlib.PurePosixPath(t).parts
        if t.startswith("/") or any(x.startswith(".") for x in parts):
            return None
        # Заметка важнее вложения (как ссылка без расширения в Obsidian):
        # «Linux 1.8», «v2.json» — узлы с точкой в имени (DS по #449).
        stem = t[:-3] if t.casefold().endswith(".md") else t
        hit = self.by_path.get(norm(stem))
        if hit is not None:
            return hit
        leaf = pathlib.PurePosixPath(stem).name
        cands = self.by_stem.get(norm(leaf), [])
        if cands:
            return cands[0]
        folder = pathlib.PurePosixPath(stem).parent.name if "/" in stem else ""
        for p in self.by_alias.get(name_key(leaf), []):
            if not folder or norm(p.parent.name) == norm(folder):
                return p
        # Вложение [[x.pdf]] — только файл на диске (GLM M9), без списка
        # расширений; stat под try: слишком длинное имя или каталог без прав
        # ронял бы весь ночной отчёт, а не ссылку.
        for cand in disk_candidates(self.root, t):
            try:
                if cand.is_file():
                    return cand
            except (OSError, ValueError):
                continue
        return None


def unlink_unresolved(text: str, resolver: LinkResolver,
                      keep: frozenset[str] | set[str] = frozenset()) -> tuple[str, list[str]]:
    """`[[цель]]` без узла → отображаемый текст (алиас после `|`, иначе имя цели).

    Огороженные блоки кода не трогаются; перенос строки внутри ссылки
    оставляется `tidy_links`; `keep` — цели, которые считаются живыми (норм-
    ключи путей без `.md`), например файлы, которые кладёт этот же перенос.
    Возвращает (текст, список снятых целей)."""
    gone: list[str] = []
    keep_n = {norm(k) for k in keep}

    def repl(m: re.Match) -> str:
        target, alias = m.group(2), m.group(4)
        t = target.strip().rstrip("\\").strip()      # `[[Цель\|Текст]]` в таблицах: слэш — не имя
        if "\n" in t or not t:
            return m.group(0)
        stem = t[:-3] if t.casefold().endswith(".md") else t
        if norm(stem) in keep_n or resolver.resolve(t) is not None:
            return m.group(0)
        gone.append(" ".join(t.split()))
        disp = (alias or "").strip() or pathlib.PurePosixPath(stem).name.strip()
        return disp or t

    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced else FULL_LINK.sub(repl, line))
    return "\n".join(out), gone
