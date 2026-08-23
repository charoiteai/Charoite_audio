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

from charoite_paths import resolve_root

ICLOUD = pathlib.Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
# Конфиг живёт в корне ДАННЫХ, а не рядом с кодом: в бандловой установке код
# лежит в read-only .app, и чтение «рядом с собой» давало пустой словарь —
# то есть дефолты вместо настроек человека. Ночная ревизия ядер так не видела
# бы выключатель профиля (ревью 19.08, второй круг DeepSeek).
# Корень данных — через канонический resolve_root: своя копия логики без
# strip()/expanduser() делала CHAROITE_ROOT=" " относительным корнем, и
# относительный graph_dir снова зависел бы от cwd (круг-1 по PR #385,
# Sonnet).
DATA_ROOT = resolve_root(__file__)
CONFIG = DATA_ROOT / "config" / "config.yaml"


def load_config() -> dict:
    """config.yaml целиком; {} — файла нет или он битый (пути fail-closed)."""
    try:
        import yaml
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# Относительный graph_dir считается от DATA_ROOT — так же, как это делает
# приложение (`AppSettings.resolvePath(_:relativeTo: charoiteRoot)`).
# Два имени одной переменной: приложение исторически читало CHAROITE_GRAPH_DIR
# (скрины и тесты на демо-графе), Python — SUFLER_GRAPH_DIR. Демон получает
# окружение приложения, поэтому обе стороны обязаны понимать оба имени с
# одним приоритетом — иначе UI показывал бы один граф, а демон писал в
# другой (круг-1 по PR #385, DeepSeek).
ENV_GRAPH = "SUFLER_GRAPH_DIR"
ENV_GRAPH_NAMES = ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR")


def resolve(raw, root: pathlib.Path | None = None) -> pathlib.Path | None:
    """Строка из конфига → путь графа. None — пусто.

    `~` раскрывается; относительный путь считается от корня данных, а не от
    текущего каталога процесса. До этого 24 места в Python читали ключ сами:
    документированный `graph_dir: demo/graph` работал у демона (приложение
    запускает его из корня данных) и ломался у ночных скриптов и launchd —
    граф писался в одно место, а искался в другом (аудит DeepSeek 16.08,
    карточка №36).
    """
    s = str(raw or "").strip()
    if not s:
        return None
    p = pathlib.Path(s).expanduser()
    if not p.is_absolute():
        p = (root or DATA_ROOT) / p
    return p


def env_override() -> str | None:
    """Значение CHAROITE_GRAPH_DIR / SUFLER_GRAPH_DIR; пробельное = не задано."""
    for name in ENV_GRAPH_NAMES:
        raw = os.environ.get(name, "")
        if raw.strip():
            return raw
    return None


def graph_dir(cfg: dict | None = None, *, env: bool = True) -> pathlib.Path | None:
    """Единственная точка ответа «где граф».

    Порядок: CHAROITE_GRAPH_DIR / SUFLER_GRAPH_DIR → `sufler.graph_dir` из
    переданного конфига (или config.yaml, если конфиг не передан) → None.
    Переменная перекрывает конфиг: тестовый прогон любого инструмента не
    должен дотягиваться до рабочего графа (аудит 04.08 — rename_meeting
    делал ровно это).
    """
    if env:
        raw = env_override()
        if raw is not None:
            return resolve(raw)
    if cfg is None:
        cfg = load_config()
    if not isinstance(cfg, dict):
        cfg = {}
    return resolve((cfg.get("sufler") or {}).get("graph_dir"))


def configured_graph() -> pathlib.Path | None:
    """sufler.graph_dir из конфига (или SUFLER_GRAPH_DIR). None — не настроен."""
    return graph_dir()


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
            if d.name.startswith("."):
                continue      # скрытое — не граф (снимки, .obsidian, .trash)
            if d.is_dir() and (d / marker).is_dir() and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def where() -> str:
    """Человеческий ответ на «а где ты вообще искал»."""
    return " и ".join(str(r) for r in roots())
