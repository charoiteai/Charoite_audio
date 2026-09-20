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
import llm  # noqa: E402

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
    Verdict.WEAK: ("Из графа и документов (vault) — СОВПАДЕНИЯ СЛАБЫЕ "
                   "по прочитанной части:"),
    Verdict.UNVERIFIED: ("Из графа и документов (vault) — подобрано по словам, семантикой "
                         "НЕ ПРОВЕРЕНО: опирайся, только если фрагмент явно о том же:"),
    Verdict.EMPTY: "",
}
ABSENCE = {
    Verdict.CONFIDENT: "",
    Verdict.WEAK: "почти ничего",
    Verdict.UNVERIFIED: "не проверено семантикой",
    Verdict.EMPTY: "пусто",
}
NODES_SHARE = 0.4      # доля бюджета блока памяти на узлы графа, когда есть и фрагменты, а выдача не уверенная


def scope_note(result: graph_search.Result) -> str:
    """Чего поиск не читал — хвостом к ответу, когда ответ отрицательный.

    Индекс памяти намеренно исключает архив встреч и копии стенограмм: без
    этого холодный обход не укладывается в секунды. Молчать об этом нельзя:
    замер 17.09 — 11 506 файлов вне индекса против 3 283 в нём, и 1 945 файлов
    архива несут строки решений. До этой правки таблицы говорили обратное
    («скорее всего в архиве ответа нет»), то есть ответ утверждал проверку
    того, что не открывалось (DS C1 / GLM C1, входной круг по №295).

    Оговорка про ФРАГМЕНТЫ, а не про ответ целиком: сводки по темам собирает
    обход ВСЕГО графа, архив включительно (`dossier.scan`), и сказать «архив не
    читался» при досье в выдаче значило бы соврать в обратную сторону (DS C2).
    Глушить оговорку по наличию досье нельзя: замер 17.09 — на 30 типовых
    запросах досье нашлись в 29 неуверенных ответах, и оговорка молчала бы
    в 29 случаях из 30."""
    if result.status is Verdict.CONFIDENT:
        return ""
    # слова об охвате — из одного форматтера с фасадом выдачи: файл, который не
    # открылся, и служебный указатель вне индекса тоже остались за индексом, и
    # молчать о них значит снова выдать непрочитанное за проверенное (DS I2,
    # круг 2 по №295; DS I2 / GLM I2 по №296). «Отдельные фрагменты» — тот же
    # термин, что в разделителе выдачи: сводка по теме собрана по всему графу
    # и под эту оговорку не попадает (GLM I2)
    gaps = graph_search.coverage_gaps(result)
    return "отдельные фрагменты графа искались без: " + ", ".join(gaps) if gaps else ""


def absence_note(result: graph_search.Result) -> str:
    """Слово в статус нити, когда подать нечего: «пусто» — только по проверенной
    выдаче, непроверенная так не называется (DS C1 r4, тот же класс, что GLM C1
    r3); причина «не проверено» — из поля результата, а не из таблицы (GLM M2 r5)."""
    note = ABSENCE[result.status]
    if result.reason:
        note = f"{note} ({result.reason})"
    scope = scope_note(result)
    return f"{note}; {scope}" if scope else note


def absence_only(result: graph_search.Result) -> bool:
    """Подавать нечего: остаётся сказать слово отсутствия, а не звать модель.

    Политика по вердикту живёт здесь, рядом с таблицами: контур спрашивает, а
    не решает сам. Сводка по теме в выдаче — уже свидетельство, и слабый ответ
    с ней уходит в пересказ, а не в «почти ничего» (GLM, круг 2 по №295)."""
    if result.empty:
        return True
    return result.status is not Verdict.UNVERIFIED and not result.dossiers


def memory_block(result: graph_search.Result | None, *, nodes: str = "", budget: int) -> str:
    """Память в промпт одним блоком: узлы графа и фрагменты архива делят бюджет
    по долям (остаток одного уходит другому), шапка и оговорка — из таблицы по
    статусу; при EMPTY фрагментов нет, при непрогретой памяти (None) — только
    узлы. Раньше узлы шли первыми под общий кап и съедали архив целиком (DS I1
    r4). При уверенной выдаче главные — фрагменты: узлам достаётся остаток, не
    доля (GLM M3 r5). Пусто — ''."""
    frags = "" if result is None or result.status is Verdict.EMPTY else result.fragments
    nodes_part = f"Из узлов графа проекта:\n{nodes}" if nodes.strip() else ""
    # шапку и оговорку считаем ТОЛЬКО когда есть что подавать: при непрогретой
    # памяти result — None, и обращение к статусу здесь роняло бы блок узлов
    frag_part = ""
    if frags:
        lead, scope = LEAD[result.status], scope_note(result)
        if lead and scope:
            lead = f"{lead[:-1]} ({scope}):" if lead.endswith(":") else f"{lead} ({scope})"
        frag_part = f"{lead}\n{frags}"
    if nodes_part and frag_part:
        n_budget = 0 if result.status is Verdict.CONFIDENT else int(budget * NODES_SHARE)
        f_budget = budget - n_budget
        n_take = min(len(nodes_part), n_budget + max(0, f_budget - len(frag_part)))
        f_take = min(len(frag_part), budget - n_take)
        return nodes_part[:n_take] + "\n\n" + frag_part[:f_take]
    return (nodes_part or frag_part)[:budget]


def warm(cfg: dict) -> graph_search.GraphSearch | None:
    """Прогрев на старте демона: обход графа и векторы блоков из кэша. None —
    граф не настроен. Вызывать из фонового потока: холодный обход рабочего
    графа — секунды, первый вопрос владельца их ждать не должен."""
    mem = graph_search.shared(cfg, embedder=llm.embedder(cfg))
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
    mem = graph_search.shared(cfg, embedder=llm.embedder(cfg))
    if mem is None:
        raise MemoryUnavailable("граф не настроен — памяти по нему нет")
    result = mem.search(query, limit=limit, snippet_chars=snippet_chars,
                        embed_timeout=max(0.5, min(6.0, timeout / 2)))
    if not result.ready:
        raise MemoryNotReady("память по графу ещё прогревается")
    return result
