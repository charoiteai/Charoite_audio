"""Загрузка пользовательского конфига с fallback для свежего клона.

Это узкий контракт партии D-П7: если ``config/config.yaml`` отсутствует,
читается ``config/config.example.yaml``. Битый, пустой или нечитаемый
пользовательский файл НЕ подменяется примером — прежние ошибки и значение
``None`` от ``yaml.safe_load`` проходят вызывающему без изменений.

Обязательный runtime-конфиг, fail-closed чтение путей и диагностика doctor
остаются отдельными контрактами у своих владельцев.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml


def load_user_or_example(root: pathlib.Path) -> Any:
    """Разобрать config.yaml, а при его отсутствии — config.example.yaml.

    Функция сознательно не валидирует тип и не заменяет пустой YAML на
    пустой dict: часть вызывающих считает ``None`` ошибкой, а два ночных
    контура применяют собственный ``or {}``. Эта политика остаётся у них.
    """
    path = root / "config" / "config.yaml"
    if not path.exists():
        path = root / "config" / "config.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))
