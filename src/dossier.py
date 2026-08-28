"""Досье — этаж между поиском и графом.

Задача. На вопрос «что в итоге по этой теме» поиск сегодня отдаёт десяток
разрозненных кусков, и модель каждый раз собирает из них ответ заново. Досье —
готовая сводка по теме: состояние, хроника, решённое, открытое — со ссылками на
узлы-источники. Поиск сначала смотрит в индекс досье и только потом лезет в граф.

Почему так, а не иначе (по ресёрчу практик 2025-2026):

* **GraphRAG (Microsoft)** строит community summaries кластеризацией Leiden по
  всему графу. Идея сводки верная — ею и живёт этот модуль. Полная пересборка
  каждую ночь не берётся: на графе в тысячу файлов это десятки минут работы
  локальной модели ради нескольких изменившихся тем.
* **LightRAG** — отсюда главное: инкрементальное обновление без пересборки всего
  и двухуровневый поиск (сущности + темы). Кластер пересобирается, только если
  изменился хоть один его источник; сравнение по хешу состава и mtime.
* **RAPTOR** строит дерево рекурсивных абстракций. Для markdown-графа лишний
  уровень: иерархия уже задана [[backlink]]-ами, и её достаточно.
* **Graphiti/Zep** — оттуда идея, что у сводки есть срок годности и инвалидирует
  её событие (новая встреча по теме), а не расписание.

Кластер здесь — ядро-хаб плюс всё, что на него ссылается: соседние ядра, встречи,
документы. Тема кластера = имя хаба. Это дёшево (обход backlink-ов, без Leiden) и
даёт ровно те границы, которые человек уже провёл руками, расставляя ссылки.

Раз в неделю полезен полный прогон (`--full`): инкрементальные обновления со
временем растаскивают границы кластеров, и общая пересборка их выравнивает.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import pathlib
import re
import unicodedata
from collections import defaultdict
from datetime import date
from redirects import is_merged as _is_merged, stub_target as _stub_target   # локальная `redirects: dict` в scan() перекрыла бы модуль

# Сколько источников максимум уходит в один запрос к модели. Больше — сводка
# начинает терять детали, а генерация упирается в контекст.
MAX_SOURCES = 14
# Кластеры меньше этого не заводим: два файла — это не тема, это пара заметок.
MIN_CLUSTER = 3
# Потолок текста одного источника в промпте.
SRC_CHARS = 2500
# Всего на промпт.
PROMPT_CHARS = 28_000

DOSSIER_DIR = "Досье"
INDEX_MD = "_ИНДЕКС.md"
INDEX_JSON = "_index.json"

_STOP = {
    "или", "если", "как", "что", "это", "для", "при", "над", "под", "про",
    "все", "весь", "вся", "его", "её", "их", "them", "the", "and", "for",
    "быть", "было", "были", "есть", "нет", "уже", "ещё", "еще", "так", "там",
    "тот", "эта", "этот", "они", "она", "оно", "мы", "вы", "он",
}


def _norm(s: str) -> str:
    """Нижний регистр, ё→е, без диакритики — общий ключ для сравнения."""
    s = unicodedata.normalize("NFKD", s.lower().replace("ё", "е"))
    return "".join(c for c in s if not unicodedata.combining(c))


def _stem(w: str) -> str:
    """Грубое отсечение русских окончаний. Полноценный стеммер тут избыточен:
    ключи нужны для отбора кандидатов, точность добирает семантика."""
    for suf in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
                "ах", "ях", "ов", "ев", "ый", "ий", "ая", "яя", "ое", "ее",
                "ы", "и", "а", "я", "о", "е", "у", "ю"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def keywords(text: str, limit: int = 24) -> list[str]:
    """Ключи темы: самые частые значимые основы. Ими индекс и матчится.

    Коды ошибок и версий (403, 999, 38-12) сохраняем: по ним ищут чаще, чем
    по словам. Имена файлов-стенограмм отбрасываем — как ключ они бесполезны,
    а место в списке занимают.
    """
    words = re.findall(r"[а-яa-z0-9][а-яa-z0-9_.-]{2,}", _norm(text))
    freq: dict[str, int] = defaultdict(int)
    for w in words:
        if w in _STOP or len(w) > 24:
            continue
        # «2026-07-24_0911_настроика_postman_ревизия_claude» и подобное
        if w.count("_") >= 2 or re.match(r"^\d{4}-\d{2}-\d{2}", w):
            continue
        freq[w if w.isdigit() else _stem(w)] += 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _match(qkey: str, key: str) -> bool:
    """Совпадение ключей: точное или по префиксу.

    «qwen» должен находить «qwen3-32b», «токен» — «токена». Без этого индекс
    промахивается на самых частых запросах.
    """
    if qkey == key:
        return True
    short, long = (qkey, key) if len(qkey) <= len(key) else (key, qkey)
    return len(short) >= 4 and long.startswith(short)


# ─────────────────────────── чтение графа ───────────────────────────

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def _title(p: pathlib.Path) -> str:
    return p.stem


def scan(graph: pathlib.Path) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """Все .md графа → карта файлов и обратные ссылки.

    Возвращает (files, backlinks): files[title] = {path, text, mtime, kind},
    backlinks[title] = множество тех, кто на него ссылается.
    """
    files: dict[str, dict] = {}
    backlinks: dict[str, set[str]] = defaultdict(set)
    # alias → канон: заглушки сами не темы, но входящие ссылки на них живые
    redirects: dict[str, str] = {}

    for p in graph.rglob("*.md"):
        rel = p.relative_to(graph)
        # служебное, бэкапы и сами досье в кластеры не берём
        if any(part.startswith(".") for part in rel.parts):
            continue
        if rel.parts[0] == DOSSIER_DIR or p.name.startswith("_"):
            continue
        if p.name.startswith("Служебное_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # Redirect-заглушка после tier3-слияния — не тема: она сохраняет
        # входящие ссылки заметок и ссылку на канон, кластер вокруг неё
        # «жив», и ночь собирала досье по мёртвому дублю рядом с каноном,
        # тратя на него запросы облака (аудит GLM 17.08). tier3.load_cores и
        # morning_brief её уже пропускают — теперь и здесь.
        if _is_merged(text):
            # Сама заглушка — не тема, но ссылки НА НЕЁ из заметок живые:
            # встреча, сославшаяся на старое имя, пропадала из кластера
            # канона, и досье собиралось без неё (аудит графа 26.08, Codex).
            # Запоминаем стрелку и переносим входящие ниже, после скана.
            # цель — из первой строки заголовка (redirects.stub_target), а не
            # первая [[ссылка]] в файле: та могла быть из frontmatter (luna I5)
            target = _stub_target(text)
            if target:
                redirects[_title(p)] = target.rsplit("/", 1)[-1].removesuffix(".md").strip()
            continue
        t = _title(p)
        kind = rel.parts[0] if len(rel.parts) > 1 else "Прочее"
        if t in files:
            # Два файла с одним именем в разных папках (Люди/CRM.md и
            # Системы/CRM.md): раньше второй молча затирал первый, и какой
            # именно выживет, зависело от порядка обхода каталога. Выбор
            # теперь детерминирован — ядро важнее (кластеры строятся вокруг
            # ядер), при равенстве побеждает более содержательный файл — и
            # виден в логе (аудит графа 26.08, Codex).
            old_meta = files[t]
            # rel в ключе — иначе при равном ранге выигрывал тот, кого
            # rglob вернул первым, и состав графа зависел от порядка обхода.
            rank = lambda meta: (meta["kind"] == "Ядра", len(meta["text"]),
                                 meta["rel"])
            new_meta = {"path": p, "rel": str(rel), "text": text,
                        "mtime": p.stat().st_mtime, "kind": kind}
            keep, drop = ((new_meta, old_meta) if rank(new_meta) > rank(old_meta)
                          else (old_meta, new_meta))
            print(f"досье: имя «{t}» занято дважды — беру {keep['rel']}, "
                  f"пропускаю {drop['rel']}")
            files[t] = keep
            continue
        files[t] = {"path": p, "rel": str(rel), "text": text,
                    "mtime": p.stat().st_mtime, "kind": kind}
    # Ссылки разбираем ВТОРЫМ проходом, когда состав files уже устоялся: при
    # дублирующемся имени первый проход успевал записать ссылки проигравшего
    # файла под общим титулом, и кластер тянул за собой связи, которых у
    # выжившего нет (круг-2 по PR #438, DS Minor 5).
    for t, meta in files.items():
        for m in LINK_RE.finditer(meta["text"]):
            target = m.group(1).split("/")[-1].strip()
            if target and target != t:
                backlinks[target].add(t)

    # Входящие ссылки с заглушек — канону. Цепочку A→B→C проходим до конца,
    # visited держит кольцо A→B→A от вечного цикла.
    for alias, first in redirects.items():
        if alias in files:
            # Имя занято живым узлом (tier3 слил «Ядра/CRM», а конвейер завёл
            # «Системы/CRM»): входящие принадлежат ему, а не старой заглушке.
            # Иначе система теряла все ссылки, а ядро обрастало чужими —
            # молча (круг-3 по PR #438, GLM Important 3).
            continue
        canon, seen = first, {alias}
        # Останавливаемся на живом узле: в цепочке A→B→C, где B — и заглушка,
        # и живой файл-тёзка, ссылки A уходили мимо B прямо к C. Та же
        # политика, что у головы цепочки (круг-4 по PR #438, DS Important 3).
        while canon in redirects and canon not in files and canon not in seen:
            seen.add(canon)
            canon = redirects[canon]
        who = backlinks.pop(alias, set())
        if who and canon in files:
            backlinks[canon] |= who - {canon}
    return files, dict(backlinks)


def clusters(files: dict[str, dict], backlinks: dict[str, set[str]],
             min_size: int = MIN_CLUSTER) -> dict[str, list[str]]:
    """Тема → список источников. Хаб — ядро, на которое ссылаются больше всего.

    Кластер строится вокруг ядра: само ядро, всё что на него ссылается, и ядра,
    на которые ссылается оно само. Это 1-hop окрестность — ровно та связность,
    которую человек уже выразил ссылками.
    """
    out: dict[str, list[str]] = {}
    for title, meta in files.items():
        if meta["kind"] != "Ядра":
            continue
        inbound = backlinks.get(title, set())
        outbound = {m.group(1).split("/")[-1].strip()
                    for m in LINK_RE.finditer(meta["text"])}
        members = {title} | inbound | {o for o in outbound if o in files}
        members = {m for m in members if m in files}
        if len(members) >= min_size:
            # Хаб — первым: build_prompt берёт первые MAX_SOURCES участников,
            # и в кластере из 14+ встреч само ядро темы (её «Статус» и «Суть»)
            # в промпт не попадало вовсе — сводка собиралась без главного
            # источника, и страдали ровно самые большие темы (аудит графа
            # 26.08, GLM). Дальше встречи по дате, остальное по имени.
            rest = sorted(members - {title},
                          key=lambda m: (files[m]["kind"] != "Встречи", m))
            out[title] = [title] + rest
    return out


def fingerprint(members: list[str], files: dict[str, dict]) -> str:
    """Отпечаток состава: состав + время правки каждого источника.

    Совпал с записанным в досье — тема не менялась, пересобирать нечего.
    Именно это делает ночной проход дешёвым: работа идёт только по изменившимся.
    """
    h = hashlib.sha256()
    for m in sorted(members):
        meta = files.get(m)
        if not meta:
            continue
        h.update(m.encode("utf-8"))
        # Микросекунды, а не целые секунды: правка, легшая в ту же секунду,
        # что и предыдущий скан, давала прежний отпечаток — «тема не менялась»,
        # и досье не пересобиралось никогда (аудит графа 26.08, Codex).
        h.update(f"{meta['mtime']:.6f}".encode())
    return h.hexdigest()[:16]


# ─────────────────────────── генерация ───────────────────────────

# Инструкция стоит ПОСЛЕ источников намеренно: на длинном контексте требования
# из начала промпта вытесняются, и модель сползает в диалог («Принято, готов
# работать. Что дальше?»). Замер 29.07: инструкция сверху — ответ-диалог,
# инструкция снизу с якорем первой строки — формат соблюдён.
PROMPT = """Ниже — узлы графа встреч по теме «{theme}»: ядра, встречи, документы.

ИСТОЧНИКИ:
{sources}

────────────────────────────────────────
ЗАДАНИЕ

Собери из источников выше одну фактическую сводку по теме «{theme}».

Требования:
- Только то, что есть в источниках. Ничего не додумывать и не обобщать.
- В конце каждого пункта — ссылка на источник в виде [[Имя файла]].
- Нет данных на раздел — поставь «—». Раздел не выдумывать.
- Числа, имена, названия систем — дословно из источников.
- Русский язык, короткие фразы, без вводных оборотов.

Запрещено: обращаться к читателю, спрашивать «что дальше», предлагать варианты
работы, писать «принято», «готов», «如果 нужно». Это документ, а не переписка.

Ответ начни СРАЗУ со строки «## Сейчас» и заполни ровно эти пять разделов:

## Сейчас
Три-пять строк: состояние темы на сегодня.

## Как пришли
Хроника по датам, от старого к новому, до восьми строк.

## Решено
Принятые решения, каждое с датой и ссылкой.

## Открыто
Незакрытое: вопросы, блокеры, невыполненные поручения.

## Кто в теме
Люди и роли, по строке на человека.
"""

# Признак того, что модель выдала документ, а не реплику в чат.
VALID_RE = re.compile(r"^\s*##\s*Сейчас", re.M)


def looks_valid(body: str) -> bool:
    """Брак ловим до записи: без этого в граф попадают «Принято, что дальше?»."""
    if not VALID_RE.search(body or ""):
        return False
    have = sum(1 for h in ("## Сейчас", "## Как пришли", "## Решено",
                           "## Открыто", "## Кто в теме") if h in body)
    return have >= 4


def trim_to_format(body: str) -> str:
    """Отрезает всё до первого «## Сейчас» — модель любит предисловия."""
    m = VALID_RE.search(body or "")
    return body[m.start():].strip() if m else (body or "").strip()


def build_prompt(theme: str, members: list[str], files: dict[str, dict]) -> str:
    parts, total = [], 0
    for m in members[:MAX_SOURCES]:
        meta = files.get(m)
        if not meta:
            continue
        body = meta["text"]
        body = re.sub(r"^---.*?^---", "", body, flags=re.S | re.M).strip()
        body = body[:SRC_CHARS]
        block = f"### [[{m}]] ({meta['kind']})\n{body}\n"
        if total + len(block) > PROMPT_CHARS:
            break
        parts.append(block)
        total += len(block)
    return PROMPT.format(theme=theme, sources="\n".join(parts))


def render(theme: str, body: str, members: list[str], files: dict[str, dict],
           fp: str, today: str) -> str:
    """Готовый файл досье: frontmatter для машины, текст для человека."""
    kinds: dict[str, list[str]] = defaultdict(list)
    for m in members:
        if m in files:
            kinds[files[m]["kind"]].append(m)

    keys = keywords(theme + " " + " ".join(members) + " " + body)
    fm = [
        "---",
        "type: досье",
        f"тема: {theme}",
        f"собрано: {today}",
        f"отпечаток: {fp}",
        f"источников: {len(members)}",
        "ключи: [" + ", ".join(keys) + "]",
        "tags: [досье, авто]",
        "---",
        "",
        f"# Досье: {theme}",
        "",
        "> Собрано автоматически ночным проходом. Правки руками сохранятся:",
        "> при пересборке раздел «Правки автора» не трогается.",
        "",
        body.strip(),
        "",
        "## Источники",
    ]
    for kind in sorted(kinds):
        links = " · ".join(f"[[{m}]]" for m in sorted(kinds[kind]))
        fm.append(f"- **{kind}:** {links}")
    fm += ["", "## Правки автора", "", "—", ""]
    return "\n".join(fm)


KEEP_RE = re.compile(r"## Правки автора\n(.*?)$", re.S)


def preserve_manual(old_text: str) -> str | None:
    """Ручные правки переживают пересборку — иначе досье никто не будет править."""
    m = KEEP_RE.search(old_text or "")
    if not m:
        return None
    kept = m.group(1).strip()
    return kept if kept and kept != "—" else None


def read_fingerprint(path: pathlib.Path) -> str:
    try:
        head = path.read_text(encoding="utf-8")[:600]
    except OSError:
        return ""
    m = re.search(r"^отпечаток:\s*(\S+)", head, re.M)
    return m.group(1) if m else ""


# ─────────────────────────── индекс ───────────────────────────

def write_index(folder: pathlib.Path, entries: list[dict]) -> None:
    """Два файла: json для поиска, md для человека и для grep.

    Индекс намеренно плоский и лексический: чтобы найти тему, не нужно ни
    эмбеддингов, ни запущенной Ollama. Семантика добирается уже поверх.
    """
    folder.mkdir(parents=True, exist_ok=True)
    # Прошлый прогон могли убить между write и replace: имя с pid больше не
    # перезаписывается следующим, и мусор жил бы в синхронизируемой папке
    # вечно (круг-4 по PR #438, DS Important 2).
    # Только ЧУЖОЕ И СТАРОЕ: сосед прямо сейчас может держать свой tmp между
    # write и replace, и снос уронил бы ему весь ночной проход
    # (круг-5 по PR #438, GLM Important 2).
    cutoff = time.time() - 3600
    for stale in list(folder.glob(f"{INDEX_JSON}.*.tmp")) + \
            list(folder.glob(f"{INDEX_MD}.*.tmp")):
        try:
            if stale.stat().st_mtime > cutoff:
                continue
            stale.unlink()
        except OSError:
            pass
    entries = sorted(entries, key=lambda e: e["тема"].lower())

    # Атомарно: индекс пишется вне замка графа (иначе занятый соседом граф
    # оставлял бы на диске свежие досье и старый индекс — поиск смотрит
    # только сюда и сутки их не видел бы, круг-2 по PR #438, DS Important 3),
    # а без tmp+replace обрыв на середине дал бы битый json.
    # Имя с pid: общий tmp давал двум одновременным прогонам смешать
    # байты и атомарно установить битый json — а load_index глотает
    # JSONDecodeError, и поиск сутки не видит ни одного досье
    # (круг-3 по PR #438, GLM Important 4).
    tmp = folder / f"{INDEX_JSON}.{os.getpid()}.tmp"
    tmp.write_text(
        json.dumps({"версия": 1, "обновлён": date.today().isoformat(),
                    "досье": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    tmp.replace(folder / INDEX_JSON)

    lines = [
        "---", "type: индекс-досье", f"обновлён: {date.today().isoformat()}",
        f"досье: {len(entries)}", "tags: [досье, индекс]", "---", "",
        "# Индекс досье", "",
        "Список готовых сводок по темам. Поиск смотрит сюда **первым**:",
        "нашлась тема — ответ собирается из досье, а в граф идём только за деталями.",
        "",
        "| Тема | Источников | Обновлено | Ключи |",
        "|---|---|---|---|",
    ]
    for e in entries:
        keys = ", ".join(e["ключи"][:8])
        lines.append(f"| [[{DOSSIER_DIR}/{e['тема']}\\|{e['тема']}]] "
                     f"| {e['источников']} | {e['собрано']} | {keys} |")
    lines += ["", "## Как этим пользоваться", "",
              "1. Ищем тему по ключам в таблице выше.",
              "2. Открываем досье — там состояние, хроника, решения, открытые вопросы.",
              "3. За подробностями идём по ссылкам из раздела «Источники».", ""]
    tmp_md = folder / f"{INDEX_MD}.{os.getpid()}.tmp"
    tmp_md.write_text("\n".join(lines), encoding="utf-8")
    tmp_md.replace(folder / INDEX_MD)


def load_index(folder: pathlib.Path) -> list[dict]:
    p = folder / INDEX_JSON
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("досье", [])
    except (OSError, json.JSONDecodeError):
        return []


def lookup(folder: pathlib.Path, query: str, limit: int = 3) -> list[dict]:
    """Подбор досье под запрос — по пересечению основ. Без сети и без моделей.

    Возвращает записи индекса с полем «счёт»; пусто — темы нет, работает
    обычный поиск по графу.
    """
    qkeys = keywords(query, limit=14)
    if not qkeys:
        return []
    hits = []
    for e in load_index(folder):
        keys = e.get("ключи", [])
        matched = {q for q in qkeys if any(_match(q, k) for k in keys)}
        if not matched:
            continue
        # доля запроса, покрытая темой, плюс вес за прямое упоминание темы
        score = len(matched) / len(qkeys)
        if _norm(e["тема"]) in _norm(query):
            score += 0.5
        hits.append({**e, "счёт": round(score, 3), "совпало": sorted(matched)})
    return sorted(hits, key=lambda e: -e["счёт"])[:limit]
