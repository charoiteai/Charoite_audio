"""Клиент brain-сервера (:8100): один способ спросить память по графу.

До партии D-П3 карты оздоровления (#402) демон собирал этот POST в трёх
местах руками — с тремя копиями JSON-тела и своей причёской folder в
каждой; вопрос длиннее 120 знаков терялся в подписи аудита (№95) именно
потому, что каждый вызов жил сам по себе. Таймауты и обработка сбоя
остаются НА ВЫЗЫВАЮЩЕМ: живой ⚡ ждёт 2.5 с, дежавю — 8 с, глубокий
контур — 6 с, и деградация у каждого своя (узлы графа / молчание /
пустая память) — это семантика контуров, не клиента.
"""
from __future__ import annotations

import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import graphs  # noqa: E402

BASE = "http://127.0.0.1:8100"


def vault_search(cfg: dict, query: str, *, limit: int, snippet_chars: int,
                 timeout: float) -> str:
    """Текст выдачи vault_search по ГРАФУ ПРОЕКТА (folder — из конфига:
    соседние личные папки Obsidian в ответы не попадают). Сбои сети и
    формата пробрасываются исключением — вызывающий деградирует по-своему.
    """
    folder = (graphs.graph_dir(cfg) or pathlib.Path("")).name
    resp = requests.post(f"{BASE}/vault_search",
                         json={"query": query, "limit": limit,
                               "folder": folder,
                               "snippet_chars": snippet_chars},
                         timeout=timeout)
    return resp.json().get("text", "")
