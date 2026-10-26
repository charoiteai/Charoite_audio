"""Маска ПДн у облачных выходов разбора после встречи и ночи (№793, этап A).

Выход в облако остаётся у себя (`cloud_review._run_locked`, ночные шаги): гейты и
сторожа `tests/test_cloud_call_sites.py` на месте. Отсюда он берёт две функции:
`mask_prompt` — до транспорта, `unmask_reply` — до записи ответа. Что в транспорт
идёт только `<m>.text` от `mask_prompt`, держит `tests/test_pii_mask_flow.py`.

Живые подсказки и облачный чат маской не закрыты (этап C №793): их держит
выключенный ключ облака, это сказано в PRIVACY.
"""
from __future__ import annotations

import pathlib

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
           known=lambda _w: False) -> tuple[pii_mask.Forms, list[str]]:
    snap = graph_nodes.NodeIndex(graph, schema=CHAROITE).snapshot_for_mask()
    people_bad = [p for p in snap.unreadable if CHAROITE.is_person_folder(p.parent.name)]
    if people_bad:
        # нечитаемый узел человека — дыра в словаре: его имя ушло бы как есть
        raise pii_mask.MaskRefused(f"не прочитались узлы людей: {len(people_bad)} — "
                                   "отправка отменена (iCloud не скачал файл?)")
    notes = [f"не прочитались узлы систем: {len(snap.unreadable)}"] if snap.unreadable else []
    nodes = [pii_mask.NodeForms(n.name, n.aliases, n.person) for n in snap.nodes
             if n.person or n.folder in SYSTEM_FOLDERS]
    stubs: dict[str, str | None] = {}
    canon_by_path = {f"{n.folder}/{n.name}.md": n.name for n in snap.nodes}
    by_stub_path = {f"{s.folder}/{s.name}.md": s for s in snap.stubs}
    names = [n.name for n in snap.nodes]
    unique_names = {nm: nm for nm in names if names.count(nm) == 1}
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
            canon = unique_names.get(pathlib.PurePosixPath(target).stem)
        stubs[s.name] = canon
    owner = str((cfg.get("sufler") or {}).get("user_name") or "").strip()
    return pii_mask.build_forms(nodes, stubs=stubs, owner=owner, common=_common(graph),
                                known=known), notes


def mask_prompt(cfg: dict, prompt: str, graph: pathlib.Path | None) -> pii_mask.Masked:
    """Промпт для облака: пути машины убраны, имена людей и систем заменены ID.

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
    import pii_ner        # natasha грузится только там, где маска действительно нужна
    forms, notes = _forms(cfg, graph, known=pii_ner.is_common)
    text = pii_mask.prepare(text)
    masked = pii_mask.mask(text, forms, pii_ner.spans(text), level=level)
    masked.notes.extend(notes)
    return masked


def unmask_reply(reply: str, masked: pii_mask.Masked) -> tuple[str, int]:
    """Ответ модели обратно в имена: `(текст, сколько токенов не развернулось)`."""
    if masked.level == "off":
        return reply, 0
    return pii_mask.unmask(reply, masked)
