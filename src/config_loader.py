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

from charoite_paths import CODE_ROOT


def load_user_or_example(root: pathlib.Path, *, code: pathlib.Path | None = None) -> Any:
    """Разобрать config.yaml, а при его отсутствии — config.example.yaml.

    Два файла — из двух РАЗНЫХ корней, и это не мелочь. Пользовательский
    конфиг принадлежит корню ДАННЫХ: его правит владелец, он уезжает вместе
    с графом и записями. Пример принадлежит корню КОДА: он приезжает с
    поставкой и в папке данных его нет вовсе.

    Пока корень данных совпадал с корнем кода (запуск из checkout), разницы
    не было видно. Как только точка входа называет свой корень данных —
    штатный режим установки и цель №327 — старый код искал пример в папке
    владельца, не находил и падал `FileNotFoundError` прямо из импорта
    модуля: ни сообщения, ни дефолтов (круг 18 по коду №327, найдено полным
    прогоном после перевода тестов на названный корень).

    Функция сознательно не валидирует тип и не заменяет пустой YAML на
    пустой dict: часть вызывающих считает ``None`` ошибкой, а два ночных
    контура применяют собственный ``or {}``. Эта политика остаётся у них.
    """
    return yaml.safe_load(chosen_path(root, code=code).read_text(encoding="utf-8"))


def chosen_path(root: pathlib.Path, *, code: pathlib.Path | None = None) -> pathlib.Path:
    """Файл, который прочтёт `load_user_or_example` при этом корне."""
    path = root / "config" / "config.yaml"
    return path if path.exists() else (code or CODE_ROOT) / "config" / "config.example.yaml"


def fingerprint(root: pathlib.Path, *, code: pathlib.Path | None = None) -> tuple:
    """Что должно измениться, чтобы конфиг стоило перечитать.

    Ключ кэша у потребителя. Живёт здесь, потому что выбор файла — знание
    этого модуля: потребитель, меривший «свой конфиг и пример» по своей копии
    правила, молча протухал бы, поменяйся правило выбора (круг 6 по коду
    №329, GLM I2).

    В ключе оба кандидата: появление собственного `config.yaml` там, где
    читался пример, — тоже причина перечитать.
    """
    def когда(p: pathlib.Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return -1.0

    свой = root / "config" / "config.yaml"
    пример = (code or CODE_ROOT) / "config" / "config.example.yaml"
    return (root, когда(свой), когда(пример))
