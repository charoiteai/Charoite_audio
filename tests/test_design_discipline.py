"""Дизайн-система живёт в коде, а не на бумаге.

Дизайн-аудит 21.08 (по Рамсу, 15/30) нашёл системную причину разброса:
токены объявлены в `Theme`/`DesignKit`, а вью красят и меряют «по месту»
— 21 вхождение `.orange`/`.green` мимо `Theme.warning`/`Theme.ok`,
13 вариантов `.system(size:)` мимо системной шкалы, поверхности
`surfaceMemory`/`surfaceCloud` с нулём вызовов. Каждое такое место —
будущая правка «в одном экране поправили, в трёх забыли».

Этот сторож держит дисциплину после уборки: новый цвет или размер по
месту — красный прогон с именем файла и строки. Системный красный записи
и ошибки (`.red`) разрешён осознанно — DESIGN.md: семантические цвета
системные, не фирменные.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
VIEWS = REPO / "app" / "Sources" / "CharoiteApp" / "Views"
DESIGN_KIT = REPO / "app" / "Sources" / "CharoiteApp" / "DesignKit.swift"

# `.orange`/`.green` как цвет: и `Color.orange`, и `.foregroundStyle(.orange)`,
# и `return .orange`. Слово внутри идентификатора (`isGreen`) не считается.
BY_PLACE_COLOR = re.compile(r"(?<![\w.])(?:Color)?\.(orange|green)\b")
# Размер шрифта цифрой вместо системного стиля.
BY_PLACE_SIZE = re.compile(r"\.system\(size:")


def _views() -> list[pathlib.Path]:
    files = sorted(VIEWS.rglob("*.swift"))
    assert files, "папка вью не найдена — тест смотрит не туда"
    return files


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    out: list[str] = []
    for path in _views():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0]
            if pattern.search(code):
                out.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    return out


def test_цвета_предупреждения_и_успеха_только_через_токены():
    assert _offenders(BY_PLACE_COLOR) == [], (
        "оранжевый и зелёный по месту — используйте Theme.warning / Theme.ok"
    )


def test_размер_шрифта_только_из_системной_шкалы():
    assert _offenders(BY_PLACE_SIZE) == [], (
        "размер цифрой — используйте .caption/.callout/.headline/.title2"
    )


def test_поверхности_происхождения_используются():
    """Токены без вызовов — правило на бумаге. Правило ревизии 08.08 обязано
    жить хотя бы на трёх экранах: память, встреча, настройки."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in _views())
    assert text.count("surfaceMemory") >= 2, "лаванда памяти не применена"
    assert text.count("surfaceCloud") + text.count("CloudSurface") >= 2, (
        "небо облака не применено"
    )


@pytest.mark.parametrize("token", ["warning", "ok", "surfaceMemory", "surfaceCloud"])
def test_токены_объявлены_один_раз(token: str):
    """Переобъявление токена в чужом файле — та же болезнь, что цвет по
    месту, только спрятанная под именем токена."""
    pattern = re.compile(rf"static let {token}\b")
    hits = [p for p in (REPO / "app" / "Sources" / "CharoiteApp").rglob("*.swift")
            if pattern.search(p.read_text(encoding="utf-8"))]
    assert len(hits) == 1, [str(h.relative_to(REPO)) for h in hits]
