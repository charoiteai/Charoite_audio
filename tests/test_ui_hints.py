"""Подсказка, которая велит что-то включить, обязана вести к существующему.

Кнопка «Claude» (⌘⇧⏎) при выключенном облаке говорит: «включите «Облако» в
настройках». В Настройках облака нет вообще — ни тумблера, ни секции. Орган
называется «Claude» и стоит в том же тулбаре, в двух сантиметрах левее самой
кнопки. Человек уходит искать несуществующий пункт, не находит и решает, что
функция сломана.

Отдельная цена именно у этой подсказки: это единственный тумблер, который
отправляет стенограмму рабочей встречи с машины. PRIVACY.md обещает opt-in,
и первое включение делает человек — значит он обязан понимать, что и где
включает. Ложный адрес превращает осознанное согласие в блуждание по меню.

Тест структурный, по исходникам: swift здесь не собрать, а инвариант всё
равно текстовый — «имя из подсказки существует, и существует там, куда
подсказка посылает».
"""
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app" / "Sources"
SETTINGS = "SettingsView.swift"

# «включите «X» в настройках» внутри строкового литерала Swift. Кавычки-ёлочки
# в комментариях не в счёт: инвариант про то, что читает пользователь.
DIRECTIVE = re.compile(r'"[^"\n]*?(?:включите|выключите)\s+«([^»]+)»([^"\n]*)"')

# Подписи органов управления: Toggle("X"/{ Text("X") }, Button("X"), Label("X"),
# Section("X"). Ими же человек ищет орган глазами на экране.
CONTROL = re.compile(r'(?:Toggle|Button|Label|Section|Text)\(\s*"([^"\n]+)"')


def _swift() -> list[pathlib.Path]:
    return sorted(APP.rglob("*.swift"))


def _controls(paths) -> set[str]:
    found: set[str] = set()
    for p in paths:
        found.update(CONTROL.findall(p.read_text(encoding="utf-8")))
    return found


def _directives() -> list[tuple[pathlib.Path, str, str]]:
    out = []
    for p in _swift():
        for name, tail in DIRECTIVE.findall(p.read_text(encoding="utf-8")):
            out.append((p, name, tail))
    return out


def test_there_is_something_to_check():
    """Страховка от тихого «тест ничего не нашёл и потому зелёный»."""
    assert _directives(), "ни одной подсказки «включите ...» — регулярка разъехалась с UI"


def test_hint_names_a_control_that_exists():
    everywhere = _controls(_swift())
    for path, name, _ in _directives():
        assert name in everywhere, (
            f"{path.name}: подсказка зовёт включить «{name}», "
            "а органа с такой подписью в приложении нет")


def test_hint_sends_to_the_screen_where_the_control_is():
    settings = _controls([p for p in _swift() if p.name == SETTINGS])
    for path, name, tail in _directives():
        if not re.search(r"в [Нн]астройках", tail):
            continue
        assert name in settings, (
            f"{path.name}: подсказка посылает за «{name}» в Настройки, "
            f"а такого органа там нет — он на другом экране")


@pytest.mark.parametrize("sample, ok", [
    ('.help("Облако выключено: включите «Claude» в настройках")', True),
    ("// включите «Облако» в настройках — это комментарий, не UI", False),
])
def test_directive_pattern_reads_only_what_the_user_sees(sample, ok):
    assert bool(DIRECTIVE.findall(sample)) is ok, sample
