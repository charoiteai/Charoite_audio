"""Заглушки-редиректы после слияния узлов — один детектор для всех слоёв.

После слияния дубль остаётся файлом «`# Имя → [[Папка/Канон]]` … Дубль.
Смерджен» — так пишет tier3. Облако помечает свои слияния своими словами
(«⚠️ **Дубль слит.**»), и три независимые проверки буквальной строки
«Дубль. Смерджен» (dossier.scan, tier3.load_cores, graph_updater.
resolve_core_path) такие заглушки принимали за живые узлы: досье собирало
кластер вокруг мёртвого дубля, а свежий «## Статус» мог уехать в заглушку
(аудит графа 28.08, Sonnet Important 5). cloud_review узнавал заглушки по
СТРУКТУРЕ — первому заголовку со стрелкой; теперь так узнают все.
"""
from __future__ import annotations

import re

MERGED_MARKER = "Дубль. Смерджен"
REDIRECT_RE = re.compile(r"^# .+? (?:→|->|⇒) \[\[[^\]]+\]\]", re.M)
STUB_TARGET_RE = re.compile(r"(?:→|->|⇒) \[\[([^\]]+)\]\]")


def stub_body(text: str) -> str:
    """Тело файла без frontmatter и ведущих HTML-комментариев — общая
    нормализация для is_redirect_stub и stub_target, чтобы стрелка в
    YAML-поле не притворялась редиректом (GLM, круг-4 M2)."""
    body = (text or "").replace("\r\n", "\n").strip()
    body = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.S)
    return re.sub(r"\A(?:\s*<!--.*?-->\s*)*", "", body, flags=re.S).lstrip()


def stub_target(text: str) -> str | None:
    """Куда указывает заглушка-редирект: `→ [[Папка/Канон]]` → «Папка/Канон.md».

    Цель берём из ТЕЛА заглушки после среза frontmatter, не по сырому тексту:
    стрелка-ссылка в YAML-поле (note, related) увела бы цель мимо канона, и
    легитимная заглушка ушла бы в карантин (DS, круг-4). Ищется в той же
    первой строке, которую валидирует is_redirect_stub."""
    first = stub_body(text).split("\n", 1)[0]
    m = STUB_TARGET_RE.search(first)
    if not m:
        return None
    target = m.group(1).split("|", 1)[0].split("#", 1)[0].strip()
    return target if target.endswith(".md") else f"{target}.md"


def is_redirect_stub(text: str) -> bool:
    """Заглушка после слияния: короткий файл, чей ПЕРВЫЙ заголовок (после
    frontmatter) — стрелка на канон. Стрелка где-то в середине переписанного
    узла заглушкой не делает (круг-1 по PR #381, Codex + DeepSeek)."""
    body = stub_body(text)
    if not body or len(body) > 4000:   # длину мерим ПОСЛЕ frontmatter; облачная
        # заглушка с перечнем слитых фактов длиннее 1200 (DS, круг-1 по #448 M8)
        return False
    first = body.split("\n", 1)[0].strip()
    return REDIRECT_RE.fullmatch(first) is not None


def is_merged(text: str) -> bool:
    """Узел уже сведён в канон: пометка tier3 ИЛИ структурная заглушка."""
    return MERGED_MARKER in text or is_redirect_stub(text)
