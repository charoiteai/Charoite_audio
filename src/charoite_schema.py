"""Значение схемы хранилища Чароита — одно место литералов имён папок и разделов.

Класс схемы живёт в пакете графа (`charoite_graph.graph_schema`), но её
значение — боевое имя папок владельца — здесь и литералами: единственная
копия, которую читает сторож литералов (`scripts/layout_map.py`) и на которую
переводятся потребители (PR B). Ссылок на константы модулей в значении нет
намеренно: пока значение собрано из констант, сторож, читающий сам вызов, не
увидел бы ни одного литерала и молчал бы о копиях, которые он и должен ловить.

Слой — graph: имя папки хранилища читают модули графа, и ничего, кроме схемы и
её значения, модуль не тянет.
"""
from __future__ import annotations

from charoite_graph.graph_schema import GraphSchema

CHAROITE = GraphSchema(
    node_folders=("Люди", "Команды", "Системы", "Модели", "Блокеры", "Ядра",
                  "People", "Teams", "Systems", "Models", "Blockers", "Cores"),
    people_folders=("Люди", "People"),
    core_folders=("Ядра", "Cores"),
    meeting_folders=("Встречи", "Встречи-архив", "Meetings"),
    dossier_dir="Досье",
    meeting_dir="Встречи",
    exclude_dirs=("Встречи-архив", "Документация/Стенограммы встреч"),
    service_prefixes=("_", "Служебное_"),
    history_heads=("## Встречи", "## Хроника", "## Meetings", "## History"),
    raw_markers=("стенограмм", "transcript"),
    raw_suffixes=("_live.md",),
    meeting_link_prefixes=("Встречи", "Meetings"),
)
