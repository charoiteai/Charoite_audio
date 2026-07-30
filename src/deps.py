"""Рецепт вместо трейсбека, когда скрипт запущен не тем интерпретатором.

Зависимости живут в `.venv` — так велит README. Но набрать `python3 scripts/…`
человек будет всё равно: по привычке, по копипасту из статьи, из старой версии
документации. Ответом на это была стена трассировки:

    Traceback (most recent call last):
      File "scripts/memory_bench.py", line 27, in <module>
        import yaml
    ModuleNotFoundError: No module named 'yaml'

Для того, кто пришёл посмотреть продукт, это выглядит как «не работает», а не
как «взят не тот питон». Установка — и без того первая причина, по которой
локальные инструменты бросают.

Модуль ставит хук на необработанные исключения: `ModuleNotFoundError`
превращается в три строки о том, что делать. Хук, а не проверка списка
пакетов, потому что список у каждого скрипта свой и он меняется — а знать
рецепт достаточно одному месту. Всё остальное (настоящие ошибки импорта в
рабочем окружении) уходит прежнему обработчику без изменений.

Никаких зависимостей у самого модуля быть не может — иначе он падал бы первым.
"""
from __future__ import annotations

import pathlib
import sys

VENV = pathlib.Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"


def hint(module: str) -> str:
    """Текст рецепта. Отдельной функцией, чтобы его можно было проверить тестом."""
    script = pathlib.Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "скрипт"
    where = ".venv/bin/python" if VENV.exists() else "python3 -m venv .venv && " \
        ".venv/bin/pip install -r requirements.txt, затем .venv/bin/python"
    return (f"Charoite: не найден пакет «{module}» — похоже, {script} запущен не тем "
            f"интерпретатором.\n"
            f"  Зависимости живут в .venv: {where} {' '.join(sys.argv[:1])}\n"
            f"  Что ещё не готово, покажет: python3 scripts/doctor.py")


def explain_missing() -> None:
    """Включить объяснение. Зовётся ДО первого импорта внешнего пакета."""
    previous = sys.excepthook

    def hook(kind, exc, tb):  # noqa: ANN001
        if issubclass(kind, ModuleNotFoundError):
            print(hint(getattr(exc, "name", None) or "?"), file=sys.stderr)
            raise SystemExit(1)
        previous(kind, exc, tb)

    sys.excepthook = hook
