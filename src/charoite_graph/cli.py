"""Командная строка пакета: `charoite-graph index|search <папка>` без приложения.

Третий вход пакета (№323 PR 2): человек с колесом ищет по своей папке заметок —
граф ссылок и русская морфология, векторы — если задан адрес модели. Окружение
вход не читает вовсе: путь папки — `resolve()` (`~` раскрывает оболочка), каталог
кэша — только флагом `--data-dir`, ширина справки — константой, а не `COLUMNS`
(входные круги 1–3 по №323). Индекс здесь строится мимо двери приложения
`graphs.open_search` законно: это второй владелец шва `GraphSearch` в
`ENV_SEAMS`, и схема у пакета своя — `PLAIN`.

Коды выхода — свои у этого входа (таблица приложения `exit_codes` пакету
недоступна):

* 0 — ответ дан (статус поиска — в выводе, а не в коде);
* 1 — `index`: векторы собраны не для всех заметок (причина — в stderr);
* 2 — аргументы: не каталог, не хватает флага;
* 3 — индекс пуст: в папке нет прочитанных заметок.

Кэш векторов хранит пути и векторы заметок: вход ставит процессу маску 0o077 на
время работы и возвращает прежнюю — всё, что создаст запись, достаётся только
владельцу. Транспорт двери — `urllib` с его правилами прокси (№525).
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import pathlib
import sys

from charoite_graph import embed_door
from charoite_graph.graph_search import GraphSearch

EXIT_OK = 0
EXIT_LEFT = 1
EXIT_USAGE = 2
EXIT_EMPTY = 3

#: Ширина справки — константа: `HelpFormatter` без неё читает `COLUMNS` (M1 входа 2).
HELP_WIDTH = 88
#: Срок вектора запроса: холодная модель на первом вопросе грузится дольше 6 с поиска демона.
QUERY_TIMEOUT_S = 30.0
#: Имя векторизатора-отказа: подписывать кэш им нечем — без адреса кэш не читается и не пишется.
NO_MODEL = "нет-модели"
REFUSED = "адрес модели не задан (--model-url) — ищу по словам"
PRIVATE_UMASK = 0o077


def _no_color(version: tuple[int, ...]) -> dict:
    """argparse с 3.14 красит справку по NO_COLOR/FORCE_COLOR/TERM — чтение окружения;
    параметра `color` до 3.14 нет, и передать его значило бы TypeError."""
    return {"color": False} if version >= (3, 14) else {}


def _parser() -> argparse.ArgumentParser:
    fmt = functools.partial(argparse.HelpFormatter, width=HELP_WIDTH)
    extra = _no_color(sys.version_info)
    ap = argparse.ArgumentParser(prog="charoite-graph", formatter_class=fmt,
                                 description="Поиск по графу ссылок папки markdown.", **extra)
    sub = ap.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", formatter_class=fmt, help="собрать векторы заметок в --data-dir", **extra)
    search = sub.add_parser("search", formatter_class=fmt, help="найти по запросу", **extra)
    for p in (index, search):
        p.add_argument("folder", help="папка заметок (markdown)")
        _common(p)
    search.add_argument("query", help="запрос")
    search.add_argument("--limit", type=int, default=4, help="сколько фрагментов показать (4)")
    search.add_argument("--json", action="store_true", help="ответ словарём JSON")
    return ap


def _common(p: argparse.ArgumentParser) -> None:
    """Флаги векторизатора — одни на все команды."""
    p.add_argument("--model-url", help="адрес сервера эмбеддингов Ollama (/api/embed), например http://127.0.0.1:11434")
    p.add_argument("--model", help="имя модели эмбеддингов; подписывает кэш векторов")
    p.add_argument("--data-dir", help="каталог кэша векторов; нужен вместе с --model-url")


def _parse(argv: list[str] | None) -> tuple[argparse.Namespace | None, int]:
    """Единственное место, где argparse может выйти: его `SystemExit` — код возврата."""
    ap = _parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return None, e.code if isinstance(e.code, int) else EXIT_USAGE
    problem = None
    if args.model_url and not (args.model and args.data_dir):
        problem = "--model-url требует --model и --data-dir"
    elif (args.model or args.data_dir) and not args.model_url:
        problem = "--model и --data-dir имеют смысл только вместе с --model-url"
    elif args.command == "index" and not args.model_url:
        problem = "index собирает векторы — нужен --model-url"
    if problem:
        print(f"charoite-graph: {problem}", file=sys.stderr)
        return None, EXIT_USAGE
    return args, EXIT_OK


def embedder_from_args(args: argparse.Namespace):
    """Векторизатор из флагов: дверь пакета по адресу или дверь-отказ без него."""
    if args.model_url:
        return embed_door.embedder(args.model_url, args.model)
    return embed_door.embedder("", NO_MODEL, refused=REFUSED)


def _open(args: argparse.Namespace) -> GraphSearch | None:
    """Индекс папки, обойдённый и с векторами из кэша; None — индекс пуст (строка в stderr)."""
    folder = pathlib.Path(args.folder).resolve()
    data_dir = pathlib.Path(args.data_dir).resolve() if args.data_dir else None
    gs = GraphSearch(folder, embedder=embedder_from_args(args), data_dir=data_dir)
    gs.refresh(force=True)
    gs.load_vectors()
    if not gs.ready:
        print(f"charoite-graph: индекс пуст — в {folder} нет прочитанных заметок .md", file=sys.stderr)
        return None
    return gs


def _index(gs: GraphSearch) -> int:
    done = gs.embed_pending()
    left = len(gs.pending_vectors())
    print(f"векторы: {done} файлов, ожидают {left}")
    if left:
        print(f"charoite-graph: {gs.note or 'векторы собраны не все'}", file=sys.stderr)
        return EXIT_LEFT
    return EXIT_OK


def _search(gs: GraphSearch, args: argparse.Namespace) -> int:
    result = gs.search(args.query, limit=args.limit, embed_timeout=QUERY_TIMEOUT_S)
    print(json.dumps(result.as_dict(), ensure_ascii=False) if args.json else result.text)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args, code = _parse(argv)
    if args is None:
        return code
    if not pathlib.Path(args.folder).resolve().is_dir():
        print(f"charoite-graph: не каталог: {args.folder}", file=sys.stderr)
        return EXIT_USAGE
    previous = os.umask(PRIVATE_UMASK)
    try:
        gs = _open(args)
        if gs is None:
            return EXIT_EMPTY
        return _index(gs) if args.command == "index" else _search(gs, args)
    finally:
        os.umask(previous)
