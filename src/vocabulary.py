"""Словарь замен для стенограмм: STT коверкает домены — чиним декларативно.

config.yaml:
  sufler:
    vocabulary:
      "чароид": "Чароит"
      "юпэй": "YuPay"

Регистронезависимо, по границам слов (кириллица/латиница/цифры).
Единая точка для живой записи, диктовки, заметок и импорта.
"""
from __future__ import annotations

import re


def compile_rules(cfg: dict) -> list[tuple[re.Pattern, str]]:
    vocab = (cfg.get("sufler") or {}).get("vocabulary") or {}
    rules: list[tuple[re.Pattern, str]] = []
    for wrong, right in vocab.items():
        w = str(wrong).strip()
        if not w:
            continue
        rules.append((re.compile(rf"(?<![\wЁё]){re.escape(w)}(?![\wЁё])",
                                 re.IGNORECASE), str(right)))
    return rules


def apply(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    for rx, right in rules:
        text = rx.sub(right, text)
    return text
