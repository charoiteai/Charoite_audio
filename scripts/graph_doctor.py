#!/usr/bin/env python3
"""Здоровье графа как памяти: детерминированный линт без модели.

    .venv/bin/python scripts/graph_doctor.py                 # текущий граф, сводка
    .venv/bin/python scripts/graph_doctor.py --all-graphs    # все графы vault (ночь)
    .venv/bin/python scripts/graph_doctor.py --examples 10   # с примерами путей
    .venv/bin/python scripts/graph_doctor.py --strict        # код 1, если есть предупреждения

Что считает: битые [[ссылки]] (экранированный `\\|` в таблицах — валиден,
псевдоним из `aliases:` шапки — живая цель, как у Obsidian), отдельно по
активным папкам и по архиву (`Встречи-архив` конвейер не перечитывает —
там лежит легаси, а не гниение живой памяти; порог предупреждения — только
по активным), ссылки с переносом строки внутри (для Obsidian мертвы), метки
диаризации среди Люди («Собеседник N» — узел, склеивающий разных людей),
сироты (узел без входящих), одноимённые узлы в разных папках (пары
Досье/Ядра и заглушки-редиректы tier3 — по замыслу, считаются отдельно),
почти-дубли по ключу имени (graph_names.name_key) и без порядка слов
(«Иван Петров» / «Петров Иван» — bag_key), покрытие _MOC.md, свежесть;
сборные узлы (голое имя при живых тёзках — graph_names.bare_namesakes, №611),
люди узлами в Системах, досье с меткой диаризации деятелем.

Граф не меняет. Отчёт — JSON для утреннего брифа (logs/graph_doctor.json
в корне данных) и сводка в stdout; полные списки сборных и чужих людей —
лента logs/graph_feed.json рядом с отчётом; новые сборные считаются от
базы logs/graph_feed_base.json — ленты итога прошлой ночи, которую сдвигает
только `--advance-base` (итоговый проход nightly.sh). Пороги предупреждений: битых > 2 %
активных ссылок, метки > 0, сироты > 5 % узлов, настоящие дубли > 0,
почти-дубли > 0. Аудит графа 28.08: 626 битых из 68 514 ссылок, 17
узлов-меток, 5 дублей, 7 почти-дублей — цифры, которых до этого скрипта
никто не видел; аудит памяти 07.09: 1316 битых, из них 73 % в архиве.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_graph import frontmatter  # noqa: E402
import graph_links  # noqa: E402
from charoite_graph import graph_names  # noqa: E402
import graph_updater  # noqa: E402
import graphs  # noqa: E402
import meeting_archive  # noqa: E402
from charoite_graph import redirects  # noqa: E402
from charoite_graph import safe_write  # noqa: E402
from charoite_paths import harden_umask, resolve_root  # noqa: E402

LINK = graph_links.LINK
HUB_DIRS = ("Люди", "Системы", "Команды", "Ядра", "Блокеры", "Модели", "Досье")
DESIGN_PAIRS = {frozenset(("Досье", "Ядра"))}   # досье на ядро — одноимённо по замыслу
THRESHOLDS = {"broken_share": 0.02, "orphan_share": 0.05}

_norm = graph_links.norm
_disk_candidates = graph_links.disk_candidates   # прежнее имя — для тестов и скриптов


def _is_stub(text: str) -> bool:
    # тот же детектор, что у tier3/досье/облака: «## Статус → в работе» — не
    # заглушка (DS, круг-1 по #448 I2); тег `дубль-слит` читает он же, из шапки,
    # а не подстрокой в первых 400 знаках (№777, n777-in1 Sonnet M9)
    return redirects.is_merged(text)


def inspect(root: pathlib.Path, examples: int = 0, prev_copies: int | None = None,
            prev_feed: dict | None = None, owner: str = "") -> dict:
    """Метрики одного графа. `examples` > 0 — добавить примеры путей. `prev_feed` —
    запись этого графа в базе — ленте итога прошлой ночи (None — базы нет): от неё
    считаются новые сборные узлы (№611). `owner` — полное имя владельца из конфига: его узел тёзкой не
    считается. `feed` в ответе — полные списки для ленты, в отчёт доктора не идут."""
    notes = graph_links.read_notes(root)
    rel = {p: p.relative_to(root).as_posix() for p in notes}
    resolver = graph_links.LinkResolver(root, notes)
    by_stem = resolver.by_stem

    inbound: collections.Counter = collections.Counter()
    linkers: dict[pathlib.Path, set[pathlib.Path]] = collections.defaultdict(set)   # кто ссылается, без повторов
    outbound: dict[pathlib.Path, int] = {}
    broken: list[tuple[str, str]] = []
    wrapped = 0
    links_active = 0
    for p, text in notes.items():
        n = 0
        archived = graph_links.is_archive(rel[p])
        for m in LINK.finditer(text):
            n += 1
            if "\n" in m.group(1):
                wrapped += 1
            tgt = resolver.resolve(m.group(1))
            if tgt is None:
                broken.append((rel[p], " ".join(m.group(1).split()).rstrip("\\")))   # `Цель\|Текст` — без слэша (GLM r2 M3)
            elif tgt != p and not p.name.startswith("_"):
                inbound[tgt] += 1                    # ссылка из указателя — не связь
                if not archived:                     # вред сейчас — ссылки активных заметок
                    linkers[tgt].add(p)
        outbound[p] = n
        if not archived:
            links_active += n
    broken_archive = [b for b in broken if graph_links.is_archive(b[0])]
    broken_active = len(broken) - len(broken_archive)
    # цель — файл, а не написание: `[[Имя]]` и `[[Люди/Имя]]` — одна цель (GLM M5)
    targets = {graph_names.name_key(pathlib.PurePosixPath(t).name) for _, t in broken}

    nodes = [p for p in notes if rel[p].split("/", 1)[0] in HUB_DIRS and "/" in rel[p]
             and not p.name.startswith("_")]          # _ЛЮДИ.md, _ЯДРА.md — указатели
    node_set = set(nodes)
    orphans = [p for p in nodes if inbound[p] == 0]
    placeholders = [p for p in nodes if rel[p].startswith("Люди/")
                    and graph_names.is_placeholder_node(p.stem)]
    groups = {k: v for k, v in by_stem.items() if len([x for x in v if x in node_set]) > 1}
    stubs = 0
    dup_real: list[list[str]] = []
    for _, ps in groups.items():
        ps = [x for x in ps if x in node_set]
        st = [x for x in ps if _is_stub(notes[x])]
        stubs += len(st)
        live = [x for x in ps if x not in st]
        folders = {rel[x].split("/", 1)[0] for x in live}
        for pair in DESIGN_PAIRS:            # досье на ядро — не дубль ядра
            if pair <= folders:
                live = [x for x in live if not rel[x].startswith("Досье/")]
        if len(live) < 2:
            continue
        dup_real.append(sorted(rel[x] for x in live))
    # почти-дубли: ключ без пунктуации — в любой папке; без порядка слов —
    # только Люди, как и свёртка в find_canonical («Реестр Витрин» и
    # «Витрин Реестр» у систем — разные вещи; DS r2 M2)
    near: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for p in nodes:
        folder = rel[p].split("/", 1)[0]
        keys = {graph_names.name_key(p.stem)}
        if folder == "Люди":
            keys.add(graph_names.bag_key(p.stem))
        for k in keys:
            if k:
                near[(folder, k)].add(p.stem)
    seen_groups: set[frozenset[str]] = set()
    near_dups: list[list[str]] = []
    for v in near.values():
        if len(v) > 1 and frozenset(v) not in seen_groups:
            seen_groups.add(frozenset(v))
            near_dups.append(sorted(v))

    moc = root / "_MOC.md"
    moc_linked: set[pathlib.Path] = set()
    if moc.exists():
        for m in LINK.finditer(moc.read_text(encoding="utf-8", errors="replace")):
            t = resolver.resolve(m.group(1))
            if t is not None and t in notes:   # вложение — не заметка, не покрытие (GLM M3)
                moc_linked.add(t)
    week = dt.datetime.now().timestamp() - 7 * 86400
    fresh = 0
    for p in notes:
        try:                       # файл мог исчезнуть между rglob и stat (забыть встречу,
            fresh += p.stat().st_mtime >= week   # миграция, iCloud) — не ронять doctor целиком
        except OSError:
            continue

    # Файлы, которые не читаются строго. `read_notes` берёт граф с заменой
    # нечитаемых байтов, поэтому такой файл выглядит целым, а на деле его текст
    # уже потерян: облачная ревизия его не тронет (правка, приносящая «U+FFFD»,
    # уходит в карантин — №263), поиск и индексы разъедутся на этом месте.
    # Доктор — справка человеку, а не гейт: он только называет файлы (№275).
    # Кандидаты — где символ вообще появился: чисто декодируемый файл его не
    # даст. Их и перечитываем байтами, вместо второго прохода по всему графу.
    not_utf8: list[tuple[str, str]] = []
    not_utf8_archive: list[tuple[str, str]] = []
    for p_, text in notes.items():
        if "\ufffd" not in text:
            continue
        try:
            p_.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            if graph_links.is_archive(rel[p_]):
                # Архив легаси: считаем и НАЗЫВАЕМ (список — единственный способ
                # найти файл с потерянным текстом, у соседа-счётчика битых ссылок
                # он есть; №192 берёт его как источник), но в брифе не кричим.
                not_utf8_archive.append((rel[p_], f"байт {exc.start}: {exc.reason}"))
                continue
            not_utf8.append((rel[p_], f"байт {exc.start}: {exc.reason}"))
        except OSError:
            continue                    # исчез между обходом и чтением — не наше дело

    # След склейки машины без псевдонима: человек снял то, что машина приписала
    # по повтору (№236/№286) — вето, машина не переклеит. Доктор называет пары
    # СПРАВКОЙ (счёт в сводке и примеры), не тревогой: «тревога → чинить» толкала
    # бы снести машинное поле уборкой — ровно то, что снимает вето (DS критика 2
    # по #576). Ходим по папкам, куда машина вправе писать след — включая демо-
    # граф на английском, не только HUB_DIRS (DS M5); следы с одним ключом имени
    # не схлопываем — считаем как вето (GLM M3).
    alias_vetoes: list[str] = []
    for p, text in notes.items():
        if p.parent.name not in graph_updater._AUTO_ALIAS_FOLDERS or p.name.startswith("_"):
            continue
        traced = [(graph_names.name_key(a), a) for a in frontmatter.list_field(text, graph_updater.AUTO_ALIASES_KEY)]
        if not traced:
            continue
        live = {graph_names.name_key(a) for a in frontmatter.aliases(text)}
        alias_vetoes += [f"{rel[p]} ← «{a}»" for k, a in traced if k not in live]

    # Сборные узлы (№611): голое имя при живых тёзках — каждый «Саша» встречи садится
    # в узел «Саша», и под ним лежат факты разных людей (замер 26.10: 45 узлов).
    # Предикат — `graph_names.bare_namesakes`, по нему же будет решать затвор на входе
    # (этап 2). Рядом — то же «чужой человек не там»: человек узлом в Системах и
    # досье, где действует метка диаризации. Только счёт и список: решает владелец.
    # Скобка с именем системы или команды при человеке — пометка, не фамилия:
    # «Илья (ОТДЕЛ)» не тёзка «отдел» и не делает Системы/ОТДЕЛ человеком.
    placeholder_set = set(placeholders)
    people = [p for p in nodes if rel[p].startswith("Люди/") and p not in placeholder_set
              and not _is_stub(notes[p])]
    tags = frozenset(p.stem.casefold().replace("ё", "е") for p in nodes
                     if rel[p].startswith(("Системы/", "Команды/")))
    index = graph_names.namesake_index((p.stem for p in people), tags)
    # сборный — тем же предикатом, что держит запись встречи (graph_updater.collective_held)
    collective = [(p, graph_names.bare_namesakes(p.stem, index, owner=owner, tags=tags)) for p in people
                  if graph_names.is_collective(p.stem, index, owner=owner, tags=tags)]
    collective = sorted(collective, key=lambda x: (-len(linkers[x[0]]), x[0].stem))
    # Человек в Системах — по полному ключу, порядку слов (DS M1) и слову имени: на
    # рабочем графе 26.10 все 4 находки — по слову («Настя», фамилия) или полному имени.
    person_keys = set()
    for p in people:
        words = graph_names.name_words(p.stem, tags)
        person_keys |= {" ".join(words), " ".join(sorted(words))}
    people_in_systems = []
    for p in nodes:
        if rel[p].startswith("Системы/") and not _is_stub(notes[p]):
            words = graph_names.name_words(p.stem, tags)   # один разбор с людьми (Haiku I1, DS M4)
            if " ".join(sorted(words)) in person_keys or (len(words) == 1 and words[0] in index):
                people_in_systems.append(p)
    dossier_labels = [p for p in nodes if rel[p].startswith("Досье/") and not _is_stub(notes[p])
                      and graph_names.has_speaker_actor(notes[p])]

    links_total = sum(outbound.values())
    rep = {
        "graph": root.name, "root": str(root), "notes": len(notes), "nodes": len(nodes), "links": links_total,
        "links_active": links_active, "broken": len(broken), "broken_active": broken_active,
        "broken_archive": len(broken_archive), "broken_targets": len(targets),
        "wrapped_links": wrapped, "orphans": len(orphans), "placeholders": len(placeholders),
        "dup_groups": len(groups), "dup_stubs": stubs, "dup_real": len(dup_real),
        "near_dups": len(near_dups), "moc": moc.exists(), "moc_linked": len(moc_linked),
        "moc_missing": len([p for p in nodes if p not in moc_linked]), "fresh_7d": fresh,
        "orphans_by_dir": dict(collections.Counter(rel[p].split("/", 1)[0] for p in orphans)),
        "not_utf8": len(not_utf8), "not_utf8_archive": len(not_utf8_archive),
        "alias_vetoes": len(alias_vetoes),
        "collective": len(collective), "collective_multi": sum(1 for _, n in collective if len(n) >= 2),
        "collective_links": len(set().union(*(linkers[p] for p, _ in collective))),   # заметка на два сборных — одна
        "people_in_systems": len(people_in_systems), "dossier_labels": len(dossier_labels),
    }
    # Конфликтные копии «Имя N» документов встреч (№361): по имени, без чтения —
    # доктор сигналит, убирает `dedup_graph.py` вторым правилом. Сигнал — на РОСТ
    # против прошлого отчёта: копии, которые человек оставил разбирать сам,
    # горели бы каждую ночь, и предупреждение перестали бы читать (критика
    # Opus круга 2). Прошлого отчёта нет — предупреждаем о любом числе.
    copies = meeting_archive.conflict_copies(root, compare=False)
    rep["conflict_copies"] = len(copies)
    warnings: list[str] = []
    if copies and (prev_copies is None or len(copies) > prev_copies):
        was = f" (было {prev_copies})" if prev_copies is not None else ""
        warnings.append(f"конфликтных копий «Имя N» в документах встреч: {len(copies)}{was} — "
                        "побайтно равные убирает dedup_graph.py --apply-copies")
    if links_active and broken_active / links_active > THRESHOLDS["broken_share"]:
        warnings.append(f"битых ссылок в активных папках {broken_active} "
                        f"({100 * broken_active / links_active:.1f} % от {links_active}; в архиве ещё {len(broken_archive)})")
    if placeholders:
        warnings.append(f"меток диаризации среди Люди: {len(placeholders)} — узлы склеивают разных людей")
    if nodes and len(orphans) / len(nodes) > THRESHOLDS["orphan_share"]:
        warnings.append(f"сирот {len(orphans)} ({100 * len(orphans) / len(nodes):.0f} % узлов)")
    if dup_real:
        warnings.append(f"одноимённых узлов в разных папках: {len(dup_real)} групп")
    if near_dups:
        warnings.append(f"почти-дублей по имени: {len(near_dups)} (пунктуация/скобки/дефис/порядок слов)")
    if not_utf8:
        warnings.append(f"файлов не в UTF-8: {len(not_utf8)} — правь в редакторе, "
                        "облако их не тронет"
                        + (f" (в архиве ещё {len(not_utf8_archive)})" if not_utf8_archive else ""))
    # Сигнал — на НОВЫЕ узлы против базы, не на число: 42 узла, известных владельцу,
    # горели бы каждую ночь, а «один слит, другой появился» число не меняет (Sonnet M3).
    # База — лента итога прошлой ночи (`base_path`): ранний и итоговый проходы ночи и
    # ручной запуск днём сравнивают с одной базой, и новое горит в каждом брифе до
    # следующей ночи (n611-s1-out2 C1). Досье пересобираются каждую ночь — их счёт
    # только в сводке и ленте, без предупреждения (критика Sonnet 2).
    collective_paths = [rel[p] for p, _ in collective]
    for key, now, text in (
            ("collective", collective_paths,
             "сборных узлов в Люди (голое имя при живых тёзках): {n}, из них с ≥ 2 тёзками "
             + str(rep["collective_multi"]) + ", на них ссылаются заметок " + str(rep["collective_links"])
             + " — под одним узлом факты разных людей"),
            ("people_in_systems", [rel[p] for p in people_in_systems], "людей узлами в Системах: {n}")):
        if prev_feed is None:
            fresh = now
        else:
            known = prev_feed.get(key)
            was = {x.get("node") if isinstance(x, dict) else x
                   for x in (known if isinstance(known, list) else [])}   # правленая руками база (Sonnet M4)
            fresh = [x for x in now if x not in was]
        if fresh:
            tail = "" if prev_feed is None else f", новых с прошлой ночи {len(fresh)}"
            warnings.append(text.format(n=len(now)) + tail + "; список — logs/graph_feed.json")
    # Мера затвора встречи (№611 этап 2): новые ссылки в УЖЕ известные сборные. Сверка
    # выше видит только новый узел, а рост `linked_from` старого — нет (n611-in2 DS C4).
    # Число — в сводке и ленте каждого прохода: база ночи сдвигается, и приёмка
    # суммирует ленты, а не ловит предупреждение (n611-in3 Sonnet I5).
    new_links = None
    if prev_feed is not None:
        known = prev_feed.get("collective")
        known = known if isinstance(known, list) else None
        was_links = {x["node"]: x["linked_from"] for x in (known or [])
                     if isinstance(x, dict) and isinstance(x.get("node"), str)
                     and isinstance(x.get("linked_from"), int)
                     and not isinstance(x.get("linked_from"), bool)}
        # база без меры (лента старого формата, битая) — «не с чем сравнить», не ноль
        # (n611-out1 Sonnet M1); пустой список сборных — честный ноль
        if known is not None and (was_links or not known):
            new_links = sum(max(0, len(linkers[q]) - was_links[p])
                            for p, (q, _) in zip(collective_paths, collective) if p in was_links)
        if new_links:
            # рост ссылок не называет источник: встреча мимо затвора, ручная ссылка
            # или ночное слияние (n611-out1 DS M3)
            warnings.append(f"новых ссылок в известные сборные узлы с прошлой ночи: {new_links} — "
                            "встреча мимо затвора, ручная ссылка или слияние; список — logs/graph_feed.json")
    rep["collective_new_links"] = new_links
    rep["warnings"] = warnings
    rep["feed"] = {
        "notes": len(notes),
        "collective_new_links": new_links,
        "collective": [{"node": p, "linked_from": len(linkers[q]), "namesakes": n}
                       for p, (q, n) in zip(collective_paths, collective)],
        "people_in_systems": [rel[p] for p in people_in_systems],
        "dossier_labels": [rel[p] for p in dossier_labels],
        "placeholders": [rel[p] for p in placeholders],
    }
    if examples:
        rep["examples"] = {
            "broken": [f"{s} -> [[{t}]]" for s, t in broken if not graph_links.is_archive(s)][:examples],
            "broken_archive": [f"{s} -> [[{t}]]" for s, t in broken_archive[:examples]],
            "placeholders": [rel[p] for p in placeholders[:examples]],
            "orphans": [rel[p] for p in orphans[:examples]],
            "dup_real": [" | ".join(g) for g in dup_real[:examples]],
            "near_dups": [" | ".join(g) for g in near_dups[:examples]],
            "not_utf8": [f"{s} — {why}" for s, why in not_utf8[:examples]],
            "not_utf8_archive": [f"{s} — {why}" for s, why in not_utf8_archive[:examples]],
            "alias_vetoes": alias_vetoes[:examples],
            "conflict_copies": [str(c.copy.relative_to(root)) for c in copies[:examples]],
            "collective": [f"{rel[p]} ← из {len(linkers[p])} заметок; тёзки: {', '.join(n)}"
                           for p, n in collective[:examples]],
            "people_in_systems": [rel[p] for p in people_in_systems[:examples]],
            "dossier_labels": [rel[p] for p in dossier_labels[:examples]],
        }
    return rep


def summary(rep: dict) -> str:
    lines = [f"{rep['graph']}: заметок {rep['notes']}, узлов {rep['nodes']}, ссылок {rep['links']}; "
             f"битых {rep['broken']} (актив {rep.get('broken_active', '?')} / архив {rep.get('broken_archive', '?')}; "
             f"целей {rep['broken_targets']}, с переносом {rep['wrapped_links']}); "
             f"сирот {rep['orphans']}; меток {rep['placeholders']}; "
             f"дублей {rep['dup_real']} (+{rep['dup_stubs']} заглушек); почти-дублей {rep['near_dups']}; "
             f"вне MOC {rep['moc_missing']}; не в UTF-8 {rep.get('not_utf8', 0)}"
             f" (архив {rep.get('not_utf8_archive', 0)}); вето склеек {rep.get('alias_vetoes', 0)}; "
             f"сборных {rep.get('collective', 0)} (≥ 2 тёзок {rep.get('collective_multi', 0)}, "
             f"ссылаются заметок {rep.get('collective_links', 0)}); "
             f"людей в Системах {rep.get('people_in_systems', 0)}; досье с меткой {rep.get('dossier_labels', 0)}; "
             f"изменено за 7 дн. {rep['fresh_7d']}"]
    lines += [f"  ⚠️ {w}" for w in rep["warnings"]]
    for kind, items in (rep.get("examples") or {}).items():
        if items:
            lines.append(f"  {kind}:")
            lines += [f"    {x}" for x in items]
    return "\n".join(lines)


def report_path() -> pathlib.Path:
    return resolve_root(__file__) / "logs" / "graph_doctor.json"


def feed_path(report: pathlib.Path) -> pathlib.Path:
    """Лента сборных узлов и чужих людей — рядом с отчётом доктора."""
    return report.with_name("graph_feed.json")


def base_path(report: pathlib.Path) -> pathlib.Path:
    """База сигнала о новых — лента на конец прошлой ночи, рядом с отчётом.
    Двигает её только итоговый проход ночи (`--advance-base`)."""
    return report.with_name("graph_feed_base.json")


def main() -> int:
    harden_umask()   # отчёт здоровья графа с примерами узлов — только владельцу (№385)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=None, help="один граф")
    ap.add_argument("--all-graphs", action="store_true", help="все графы vault с папкой «Ядра»")
    ap.add_argument("--examples", type=int, default=0, help="сколько примеров путей печатать")
    ap.add_argument("--json", type=pathlib.Path, default=None, help="куда класть отчёт (лента graph_feed.json — рядом)")
    ap.add_argument("--strict", action="store_true", help="код 1 при предупреждениях")
    ap.add_argument("--advance-base", action="store_true",
                    help="сдвинуть базу сигнала о новых на эту ленту (итоговый проход ночи)")
    a = ap.parse_args()
    found = ([a.graph] if a.graph else graphs.all_graphs("Ядра") if a.all_graphs
             else [graphs.graph_dir(graph_updater.load_cfg()) or pathlib.Path.cwd()])
    found = [g for g in found if g and g.is_dir()]
    if not found:
        print("графов не найдено — нечего проверять")
        return 0
    try:                               # узел владельца — не тёзка (№611); нет конфига — без исключения
        owner = str((graph_updater.load_cfg().get("sufler") or {}).get("user_name") or "")
    except Exception as e:  # noqa: BLE001 — справка, не гейт: доктор без конфига считает всех
        print(f"владелец не определён ({type(e).__name__}) — его узел посчитан наравне")
        owner = ""
    out = a.json or report_path()

    def load_graphs(path: pathlib.Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("graphs")
        except (OSError, ValueError, AttributeError):
            return {}
        return value if isinstance(value, dict) else {}

    def entry(store: dict, g: pathlib.Path) -> dict | None:
        value = store.get(str(g))
        return value if isinstance(value, dict) else None   # правленая руками запись — как нет записи (DS M1)

    prev = load_graphs(out)            # прошлый отчёт — база для сигнала о росте копий
    base_file = base_path(out)
    base = load_graphs(base_file)      # база для сигнала о новых сборных — итог прошлой ночи

    def prev_copies(g: pathlib.Path) -> int | None:
        value = (entry(prev, g) or {}).get("conflict_copies")
        return value if isinstance(value, int) else None

    reps = {str(g): inspect(g, a.examples, prev_copies(g), entry(base, g), owner)   # ключ — путь: две «Работа» не затрут друг друга (GLM M11)
            for g in found}
    feeds = {g: rep.pop("feed") for g, rep in reps.items()}
    for rep in reps.values():
        print(summary(rep))
    now = dt.datetime.now().isoformat(timespec="seconds")
    # Лента — отдельным файлом рядом с отчётом: ночь при сбое доктора стирает отчёт
    # (nightly.sh), а полные списки отчёту не нужны — бриф читает только счёт (№611).
    # Все записи атомарные: читатель посреди записи не увидит обрезанный JSON.
    # Порядок: отчёт → лента → база. Отчёт не записан — код 1 и база стоит: сигнал
    # о новых не уходит в базу, не дойдя до брифа (n611-s1-out2 Sonnet I2). Лента не
    # записана — база тоже стоит: она производная записанной ленты (out3 DS I1). База
    # обновляется только по графам этого прогона: граф, не прочитанный этой ночью
    # (iCloud, `--graph`), сохраняет свою запись (Haiku I5); граф, прочитанный меньше
    # чем на 80 % прошлого, — тоже: файлы выгружены, неполная лента сделала бы
    # завтра «новыми» все узлы (out3 Sonnet I4).
    for path, body in ((out, {"generated": now, "graphs": reps}), (feed_path(out), {"generated": now, "graphs": feeds})):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            safe_write.write_text(path, json.dumps(body, ensure_ascii=False, indent=1))
        except OSError as e:
            print(f"{path.name} не записан ({e})")
            if path == out:
                return 1
            a.advance_base = False
    if a.advance_base:
        moved = dict(base)
        for g, feed in feeds.items():
            was = (entry(base, pathlib.Path(g)) or {}).get("notes")
            if isinstance(was, int) and feed["notes"] < 0.8 * was:
                print(f"база {g} не сдвинута: заметок {feed['notes']} против {was} прошлой ночи")
                continue
            moved[g] = feed
        try:
            safe_write.write_text(base_file, json.dumps({"generated": now, "graphs": moved},
                                                        ensure_ascii=False, indent=1))
        except OSError as e:
            print(f"{base_file.name} не записан ({e})")
    return 1 if a.strict and any(r["warnings"] for r in reps.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
