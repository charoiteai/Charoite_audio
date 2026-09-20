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
import sys
import time

from charoite_paths import resolve_root

ICLOUD = pathlib.Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"


def data_root() -> pathlib.Path:
    """Где лежат данные человека — спрашиваем канон, своего ответа нет.

    Хозяин корня — точка входа, которая его назвала (`charoite_paths.use_data_root`),
    а хранит его канон путей: корень один на процесс и спрашивают его все
    слои. Пока сеттер стоял здесь, в графовом модуле, процесс мог разъехаться
    сам с собой — граф читал названный корень, а слой моделей выводил свой из
    положения файла, и расхождение было немым (круг 1 по коду №327, DS C2).

    Порядок ответа канона: названный корень → `CHAROITE_ROOT` → положение
    файла. Последнее верно, только пока модуль лежит в дереве репозитория; из
    установленного пакета оно дало бы `site-packages`, то есть дефолты вместо
    настроек человека.
    """
    return resolve_root(__file__)


def config_path() -> pathlib.Path:
    """Конфиг живёт в корне ДАННЫХ, а не рядом с кодом.

    В бандловой установке код лежит в read-only `.app`, и чтение «рядом с
    собой» давало пустой словарь — то есть дефолты вместо настроек человека:
    ночная ревизия ядер так не видела бы выключатель профиля (ревью 19.08,
    второй круг DeepSeek). Функция, а не константа: корень теперь известен не
    на импорте, а когда его задал вызывающий.
    """
    return data_root() / "config" / "config.yaml"


def load_config() -> dict:
    """config.yaml целиком; {} — файла нет или он битый (пути fail-closed)."""
    try:
        import yaml
        return yaml.safe_load(config_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# Относительный graph_dir считается от корня данных — так же, как это делает
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
        p = (root or data_root()) / p
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
    """Папки, в которых лежат графы.

    CHAROITE_GRAPH_DIR / SUFLER_GRAPH_DIR сужает обход до vault этого графа
    (папки над ним): iCloud-каталог с переменной не читается. Иначе тестовый
    прогон с подменённым графом всё равно дотягивался до рабочих графов:
    forget_meeting.plan(graph=None) читал 14 651 файл из iCloud за один
    тест, а под нагрузкой 07.09 это стало 120-секундным таймаутом (№197).
    С непустой переменной graph_dir() всегда отдаёт путь, поэтому gd здесь
    не None.
    """
    if env_override() is not None:
        return [graph_dir().parent]
    gd = configured_graph()
    return ([gd.parent] if gd else []) + [ICLOUD]


def _listdir_patient(root: pathlib.Path, attempts: int = 3, pause: float = 0.5) -> list[pathlib.Path]:
    """Листинг корня с повтором: iCloud-каталог отвечал EINTR посреди ночи
    (17.08), и один сигнал ронял весь ночной шаг (аудит GLM/DS 05.09).
    Устойчивый отказ — пропуск корня вслух, не падение."""
    for i in range(attempts):
        try:
            return sorted(root.iterdir())
        except OSError as e:
            if i + 1 == attempts:
                print(f"graphs: каталог {root} не прочитался ({e}) — пропуск", file=sys.stderr)
                return []
            time.sleep(pause)
    return []


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
        for d in _listdir_patient(root):
            if d.name.startswith("."):
                continue      # скрытое — не граф (снимки, .obsidian, .trash)
            if d.is_dir() and (d / marker).is_dir() and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def where() -> str:
    """Человеческий ответ на «а где ты вообще искал»."""
    return " и ".join(str(r) for r in roots())
