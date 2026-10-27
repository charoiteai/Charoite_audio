"""Маска ПДн у облачных выходов разбора после встречи и ночи (№793, этап A).

Выход в облако остаётся у себя (`cloud_review._run_locked`, ночные шаги): гейты и
сторожа `tests/test_cloud_call_sites.py` на месте. Отсюда он берёт две функции:
`mask_prompt` — до транспорта, `unmask_reply` — до записи ответа. Что в транспорт
идёт только `<m>.text` от `mask_prompt`, держит `tests/test_pii_mask_flow.py`.

Живые подсказки и облачный чат маской не закрыты (этап C №793): их держит
выключенный ключ облака, это сказано в PRIVACY.
"""
from __future__ import annotations

import collections
import pathlib
import tempfile
from typing import Sequence

import pii_mask
import privacy
from charoite_graph import graph_nodes
from charoite_schema import CHAROITE

#: Слова, которые владелец разрешил отправлять как есть (общие термины, совпавшие с
#: именами систем): одно слово в строке, `#` — комментарий. Лежит в корне графа.
COMMON_FILE = "_маска_общие.md"
#: Папки узлов-«систем» для маски: команды и системы. Ядра, блокеры, модели — темы,
#: а не имена, их маска не трогает.
SYSTEM_FOLDERS = ("Системы", "Systems", "Команды", "Teams")


def _common(graph: pathlib.Path) -> set[str]:
    try:
        text = (graph / COMMON_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as e:
        raise pii_mask.MaskRefused(f"{COMMON_FILE} не прочитался ({type(e).__name__})") from None
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")}


def _forms(cfg: dict, graph: pathlib.Path,
           known=lambda _w: False, lexeme=None) -> tuple[pii_mask.Forms, list[str]]:
    snap = graph_nodes.NodeIndex(graph, schema=CHAROITE).snapshot_for_mask()
    nodes = [pii_mask.NodeForms(n.name, n.aliases, n.person) for n in snap.nodes
             if n.person or n.folder in SYSTEM_FOLDERS]
    if not any(n.person for n in nodes):
        # граф есть, а людей в нём нет (синхронизация не доехала, не тот каталог):
        # одной модели имён мало — замер 26.10: 88 из 140 (n793-out1 S-I1)
        raise pii_mask.MaskRefused("в графе нет узлов людей — словаря имён нет, отправка отменена; "
                                   "выход: узел человека в папке людей графа (n793-out2 S-I3; "
                                   "выключать маску отказ не советует — n793-out3 H-M2)")
    notes = []
    bad = sorted(p for p in snap.unreadable
                 if CHAROITE.is_person_folder(p.parent.name) or p.parent.name in SYSTEM_FOLDERS)
    if bad:
        # нечитаемый узел: имя — из имени файла, без псевдонимов; отказывать не за что
        # (n793-out1 DS-C2: отказ останавливал облако, система уходила как есть)
        nodes += [pii_mask.NodeForms(p.stem, (), CHAROITE.is_person_folder(p.parent.name))
                  for p in bad]
        # notes идут в `summary()` — строку лога «без имён»: только счёт (n793-out19 S-C1 = DS-C1)
        notes.append(f"не прочитались узлы ({len(bad)}), маска по имени файла")
    notes.append(f"словарь: людей {sum(n.person for n in nodes)}, "
                 f"систем {sum(not n.person for n in nodes)}")
    stubs: list[tuple[str, str | None, bool]] = []
    canon_by_path = {f"{n.folder}/{n.name}.md": n.name for n in snap.nodes}
    by_stub_path = {f"{s.folder}/{s.name}.md": s for s in snap.stubs}
    counts = collections.Counter(n.name for n in snap.nodes)
    unique_names = {nm for nm, k in counts.items() if k == 1}
    for s in snap.stubs:
        if not (CHAROITE.is_person_folder(s.folder) or s.folder in SYSTEM_FOLDERS):
            continue
        # цепочка A→B→C после месяца слияний; кольцо или обрыв — без цели
        target, seen = s.target, {f"{s.folder}/{s.name}.md"}
        while target in by_stub_path and target not in seen:
            seen.add(target)
            target = by_stub_path[target].target
        canon = canon_by_path.get(target) if target else None
        if canon is None and target:
            # ссылка без папки («[[Канон]]»): по голому имени, только если оно одно
            stem = pathlib.PurePosixPath(target).stem
            canon = stem if stem in unique_names else None
        stubs.append((s.name, canon, CHAROITE.is_person_folder(s.folder)))
    owner = str((cfg.get("sufler") or {}).get("user_name") or "").strip()
    return pii_mask.build_forms(nodes, stubs=stubs, owner=owner, common=_common(graph),
                                known=known, lexeme=lexeme), notes


def empty_workdir() -> pathlib.Path:
    """Пустой рабочий каталог облачного CLI под маской: путь без имени владельца
    (CLI кладёт cwd в системный промпт), инструментов у модели нет. Каталог не создать —
    `pii_mask.MaskRefused`: вызывающий уже умеет «ничего не отправлено» (n793-out11 S-I1)."""
    d = pathlib.Path(tempfile.gettempdir()) / "charoite-cloud-empty"
    try:
        d.mkdir(exist_ok=True)
    except OSError as e:
        raise pii_mask.MaskRefused(f"пустой рабочий каталог облака не создан ({type(e).__name__}) — "
                                   "отправка отменена") from None
    return d


def _regions(text: str, data: Sequence[str]) -> list[tuple[int, int]] | None:
    """Где в промпте данные: все вхождения каждого куска, после той же чистки путей
    машины, что прошёл промпт. None — маска по всему тексту (лишняя замена в инструкции
    дешевле имени, ушедшего открытым): кусок не нашёлся или данных не передали вовсе —
    пустой список означал бы «маскировать нечего» (n793-out3 S-I1, DS-I1)."""
    out = []
    for d in data:
        d = pii_mask.prepare(privacy.scrub_local_paths(d)[0])
        if not d.strip():
            continue
        start = text.find(d)
        if start < 0:
            return None
        # все вхождения, перекрытия тоже. Вхождений непустого куска (пустые отсеяны выше) не
        # больше длины текста — потолок шагов их не режет, полноту при `len(text)` вхождениях
        # держит тест. Без потолка мутант `start + 1` → `+ 0` растил список без конца и снимал
        # раннер CI раньше таймаута теста (PR #860, n793-out15)
        for _ in range(len(text)):
            if start < 0:
                break
            out.append((start, start + len(d)))
            start = text.find(d, start + 1)
    return out or None


def mask_prompt(cfg: dict, prompt: str, graph: pathlib.Path | None, *,
                data: Sequence[str]) -> pii_mask.Masked:
    """Промпт для облака: пути машины убраны, имена людей и систем заменены ID.

    `data` — куски промпта с данными (стенограмма, заметки, узлы, тема): маска идёт
    только по ним, инструкция — наша и уходит как есть.

    Бросает `pii_mask.MaskRefused` — вызывающий не отправляет ничего. При
    `sufler.cloud_mask: false` — промпт как есть (только без путей машины)."""
    text, _n = privacy.scrub_local_paths(prompt)
    if not privacy.mask_required(cfg):
        return pii_mask.passthrough(text)
    try:
        level = privacy.mask_level(cfg)
    except privacy.PrivacyRefused as e:
        raise pii_mask.MaskRefused(str(e)) from None
    if level != "basic":
        raise pii_mask.MaskRefused(f"уровень маски {level} ещё не поставляется (этап B) — "
                                   "sufler.cloud_mask_level: basic")
    if graph is None or not graph.is_dir():
        # без графа нет словаря людей: одной модели имён мало (замер 26.10)
        raise pii_mask.MaskRefused("граф недоступен — словаря имён нет, отправка отменена")
    try:
        import pii_ner    # natasha грузится только там, где маска действительно нужна
        import pii_fake   # pymorphy3 — тоже: лексема решает канон узла и подделки
        forms, notes = _forms(cfg, graph, known=pii_ner.is_common, lexeme=pii_fake.same_lexeme)
        text = pii_mask.prepare(text)
        ner = pii_ner.spans(text)
        ner += pii_mask.rare_word_spans(text, ner, pii_ner.is_common)
        regions = _regions(text, data)
        masked = pii_mask.mask(text, forms, ner, level=level, regions=regions)
        # метки → подставные имена: легенды нет, модель видит обычный текст (№799, плечо C)
        pii_fake.dress(masked, text, forms)
    except pii_mask.MaskRefused:
        raise
    except Exception as e:  # noqa: BLE001 — дверь: любой сбой маски — отказ
        # маска либо `Masked`, либо отказ — третьего нет (n793-out1 DS-8)
        raise pii_mask.MaskRefused(f"маска упала ({type(e).__name__}) — отправка отменена") from None
    masked.notes.extend(notes)
    if regions is None:
        masked.notes.append("куски данных в промпте не нашлись — маска по всему промпту")
    return masked


def unmask_reply(reply: str, masked: pii_mask.Masked) -> tuple[str, int]:
    """Ответ модели обратно в имена: `(текст, сколько токенов не развернулось)`."""
    if masked.level == "off":
        return reply, 0
    if masked.dress is not None:
        import pii_fake
        return pii_fake.undress(reply, masked.dress, sent=masked.text)
    return pii_mask.unmask(reply, masked)
