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
Verdict = graph_search.Verdict                  # состояние выдачи: confident / weak / unverified / empty

# Политика «что честно сказать» — одна таблица на все контуры демона: шапка
# блока в промпте, оговорка в строку промпта, слово в статус нити, когда подать
# нечего. Полная по членам Verdict — новый статус без строки здесь падает
# KeyError в тесте, а не молча берёт дефолт. До круга 4 по #577 каждый контур
# толковал Verdict своим if/elif с рукописными оговорками: один забыл EMPTY,
# другой резал фрагменты капом, третий терял досье (DS r4 I1–I3, критика 2).
LEAD = {
    Verdict.CONFIDENT: "Из графа и документов (vault):",
    Verdict.WEAK: ("Из графа и документов (vault) — СОВПАДЕНИЯ СЛАБЫЕ, "
                   "скорее всего в архиве ответа нет:"),
    Verdict.UNVERIFIED: ("Из графа и документов (vault) — подобрано по словам, семантикой "
                         "НЕ ПРОВЕРЕНО: опирайся, только если фрагмент явно о том же:"),
    Verdict.EMPTY: "",
}
CAVEAT = {
    Verdict.CONFIDENT: "",
    Verdict.WEAK: "совпадения слабые — скорее всего в архиве этого нет",
    Verdict.UNVERIFIED: "подобрано по словам, семантикой не проверено — только явно о том же",
    Verdict.EMPTY: "",
}
ABSENCE = {
    Verdict.CONFIDENT: "",
    Verdict.WEAK: "почти ничего",
    Verdict.UNVERIFIED: "не проверено семантикой (модель занята)",
    Verdict.EMPTY: "пусто",
}
NODES_SHARE = 0.4      # доля бюджета блока памяти на узлы графа, когда есть и фрагменты


def caveat(result: graph_search.Result) -> str:
    """Оговорка к фрагментам для строки промпта («…{caveat}»); пусто — без оговорки."""
    return CAVEAT[result.status]


def absence_note(result: graph_search.Result) -> str:
    """Слово в статус нити, когда подать нечего: «пусто» — только по проверенной
    выдаче, непроверенная так не называется (DS C1 r4, тот же класс, что GLM C1 r3)."""
    return ABSENCE[result.status]


def memory_block(result: graph_search.Result | None, *, nodes: str = "", budget: int) -> str:
    """Память в промпт одним блоком: узлы графа и фрагменты архива делят бюджет
    по долям (остаток одного уходит другому), шапка и оговорка — из таблицы по
    статусу; при EMPTY фрагментов нет, при непрогретой памяти (None) — только
    узлы. Раньше узлы шли первыми под общий кап и съедали архив целиком (DS I1
    r4). Пусто — ''."""
    frags = "" if result is None or result.status is Verdict.EMPTY else result.fragments
    nodes_part = f"Из узлов графа проекта:\n{nodes}" if nodes.strip() else ""
    frag_part = f"{LEAD[result.status]}\n{frags}" if frags else ""
    if nodes_part and frag_part:
        n_budget = int(budget * NODES_SHARE)
        f_budget = budget - n_budget
        n_take = min(len(nodes_part), n_budget + max(0, f_budget - len(frag_part)))
        f_take = min(len(frag_part), budget - n_take)
        return nodes_part[:n_take] + "\n\n" + frag_part[:f_take]
    return (nodes_part or frag_part)[:budget]


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
                 timeout: float) -> graph_search.Result:
    """Выдача по ГРАФУ ПРОЕКТА как значение: `status` (Verdict) — уверенно /
    слабо / не проверено семантикой / пусто, `fragments` — досье и фрагменты без
    шапок и маркеров для промпта, `text` — тот же вид, что отдавал сервер памяти,
    для человека. Состояние — полем, а не префиксом строки: досье перед «⚠» и
    «Ничего не найдено» без семантики ломали разбор строки у трёх контуров
    (круг 3 по #577, DS C1 / GLM C1). Непрогретый индекс — MemoryNotReady,
    ненастроенный граф — MemoryUnavailable (оба RuntimeError): вызывающий
    деградирует по-своему и различает «подождать» и «не будет» (GLM M8 по #577).
    `timeout` — потолок на вектор запроса: половина бюджета вызывающего, чтобы
    лексика успела в любом случае."""
    mem = graph_search.shared(cfg)
    if mem is None:
        raise MemoryUnavailable("граф не настроен — памяти по нему нет")
    result = mem.search(query, limit=limit, snippet_chars=snippet_chars,
                        embed_timeout=max(0.5, min(6.0, timeout / 2)))
    if not result.ready:
        raise MemoryNotReady("память по графу ещё прогревается")
    return result
