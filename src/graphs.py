"""Где лежат графы — одно место решения для скриптов ночного контура.

Vault — это папка НАД графом: `sufler.graph_dir` указывает на ~/Vault/Работа,
а ночью надо обойти и ~/Vault/Личное. Смотрим туда и в стандартный
iCloud-Obsidian: одного захардкоженного пути мало для машины, где Obsidian
живёт не в iCloud, — а раньше ровно его отсутствие валило первый шаг ночной
джобы. «Обходить нечего» — пустой список, а не авария.
"""
from __future__ import annotations

import os
import pathlib

ICLOUD = pathlib.Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def load_config() -> dict:
    """config.yaml целиком; {} — файла нет или он битый (пути fail-closed)."""
    try:
        import yaml
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def configured_graph() -> pathlib.Path | None:
    """sufler.graph_dir из конфига. None — не настроен или конфига нет.

    SUFLER_GRAPH_DIR перекрывает конфиг: тестовый прогон любого инструмента
    не должен дотягиваться до рабочего графа (аудит 04.08 — rename_meeting
    делал ровно это).
    """
    env = os.environ.get("SUFLER_GRAPH_DIR", "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    try:
        import yaml
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        gd = ((cfg.get("sufler") or {}).get("graph_dir") or "").strip()
    except Exception:
        return None
    return pathlib.Path(gd).expanduser() if gd else None


def roots() -> list[pathlib.Path]:
    """Папки, в которых лежат графы."""
    gd = configured_graph()
    return ([gd.parent] if gd else []) + [ICLOUD]


def all_graphs(marker: str) -> list[pathlib.Path]:
    """Графы vault, у которых есть подпапка marker («Ядра», «Встречи-архив»).

    Маркер разный, потому что скриптам нужно разное: ревизии — папка ядер,
    брифу — архив встреч. Граф из двух vault-ов подряд не дублируется.
    """
    out: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for root in roots():
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / marker).is_dir() and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def where() -> str:
    """Человеческий ответ на «а где ты вообще искал»."""
    return " и ".join(str(r) for r in roots())
