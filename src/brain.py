"""Память по графу для демона: один способ спросить «что мы знаем по теме».

До №250 это был клиент отдельного сервера памяти (:8100): три контура демона
ждали HTTP (мгновенный ответ 2,5 с, дежавю 8 с, глубокий контур 6 с) и без
сервера деградировали до узлов графа по стемам. Замер 16.09 на рабочем графе:
сервер отвечал 2,6–22 с на запрос — в бюджет мгновенного ответа он не
укладывался почти никогда. Теперь память живёт в процессе демона
(src/graph_search.py): индекс файлов графа прогревается на старте (секунды),
запрос стоит десятки миллисекунд лексики плюс один вектор запроса, если
Ollama свободна. Сеть и второй сервер не нужны.

Таймауты и обработка сбоя остаются НА ВЫЗЫВАЮЩЕМ, как и раньше: непрогретый
индекс — исключение, и деградация у каждого контура своя (узлы графа /
молчание / пустая память) — это семантика контуров, не памяти.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import graph_search  # noqa: E402

MemoryNotReady = graph_search.NotReady          # индекс прогревается — попробовать позже
MemoryUnavailable = graph_search.Unavailable    # граф не настроен — памяти не будет


def warm(cfg: dict) -> graph_search.GraphSearch | None:
    """Прогрев на старте демона: обход графа и векторы блоков из кэша. None —
    граф не настроен. Вызывать из фонового потока: холодный обход рабочего
    графа — секунды, первый вопрос владельца их ждать не должен."""
    mem = graph_search.shared(cfg)
    if mem is None:
        return None
    mem.refresh(force=True)
    mem.load_vectors()
    return mem


def vault_search(cfg: dict, query: str, *, limit: int, snippet_chars: int,
                 timeout: float) -> str:
    """Текст выдачи по ГРАФУ ПРОЕКТА — в том же виде, что отдавал сервер памяти
    («Найдено…», «⚠ …» при слабых совпадениях, «Ничего не найдено по …»).
    Непрогретый индекс — MemoryNotReady, ненастроенный граф — MemoryUnavailable
    (оба RuntimeError): вызывающий деградирует по-своему и различает «подождать»
    и «не будет» (GLM M8 по #577). `timeout` — потолок на вектор запроса: половина
    бюджета вызывающего, чтобы лексика успела в любом случае."""
    mem = graph_search.shared(cfg)
    if mem is None:
        raise MemoryUnavailable("граф не настроен — памяти по нему нет")
    result = mem.search(query, limit=limit, snippet_chars=snippet_chars,
                        embed_timeout=max(0.5, min(6.0, timeout / 2)))
    if not result.ready:
        raise MemoryNotReady("память по графу ещё прогревается")
    return graph_search.render(result, query)
