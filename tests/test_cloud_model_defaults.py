"""Один ключ конфига — один дефолт. Иначе поведение зависит от места вызова.

Пример конфига — единственная документация по ключам (это уже закреплено в
tests/test_config_example.py). Значит и значение по умолчанию обязано быть
одно: то, которое там написано. Было не так — один ключ `cloud_model` имел
разные дефолты в разных точках выхода:

    src/graph_updater.py:709          claude-opus-4-8
    scripts/nightly_claude_cores.py   claude-opus-5
    scripts/nightly_dossier_review.py claude-opus-5

и `cloud_live_model` расходился с примером в классе модели:

    config/config.example.yaml        claude-haiku-4-5-20251001
    src/daemon.py:669                 claude-sonnet-5

Сценарий: человек скопировал старый конфиг или удалил строку с моделью (либо
собрал свой урезанный YAML — это нормально для локального инструмента). Разбор
встречи пойдёт в одну модель, ночная ревизия ядер — в другую, живые ответы — в
третью, дороже той, что обещана примером. Ни один документ такого сочетания не
описывает, и понять, почему ответы разного качества, нельзя.

Отсюда: дефолты живут в одном месте (src/cloud.py) и совпадают с примерами
конфига на всех языках. Литералов модели в точках выхода нет — иначе они снова
разъедутся, и никакой тест этого не заметит.
"""
import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cloud  # noqa: E402

EXAMPLES = sorted((REPO / "config").glob("config.example*.yaml"))
# Литерал имени модели: «claude-…» в кавычках любого вида.
_LITERAL = re.compile(r"""["'](claude-[a-z0-9.\-]+)["']""")


def test_examples_are_found():
    assert len(EXAMPLES) >= 3, f"примеры конфига перестали находиться: {EXAMPLES}"


def test_every_default_matches_every_example_config():
    """Дефолт в коде и значение в примере — одно и то же, на всех языках."""
    for path in EXAMPLES:
        sufler = yaml.safe_load(path.read_text(encoding="utf-8"))["sufler"]
        for key, default in cloud.DEFAULTS.items():
            assert sufler.get(key) == default, (
                f"{path.name}: {key} = {sufler.get(key)!r}, "
                f"а код без ключа возьмёт {default!r}")


def test_example_covers_every_default_and_nothing_extra():
    """Ключи моделей в примере и в коде — одно множество."""
    sufler = yaml.safe_load(EXAMPLES[0].read_text(encoding="utf-8"))["sufler"]
    in_example = {k for k in sufler if k.startswith("cloud_") and k.endswith("_model")}
    in_example |= {"cloud_model"} if "cloud_model" in sufler else set()
    assert in_example == set(cloud.DEFAULTS), (
        f"разошлись: только в примере — {sorted(in_example - set(cloud.DEFAULTS))}, "
        f"только в коде — {sorted(set(cloud.DEFAULTS) - in_example)}")


def test_config_value_wins_over_default():
    cfg = {"sufler": {"cloud_model": "claude-sonnet-5"}}
    assert cloud.model(cfg, "cloud_model") == "claude-sonnet-5"


def test_empty_or_missing_value_falls_back_to_the_documented_default():
    for cfg in ({"sufler": {}}, {}, {"sufler": {"cloud_model": ""}},
                {"sufler": {"cloud_model": None}}, {"sufler": {"cloud_model": "   "}}):
        assert cloud.model(cfg, "cloud_model") == cloud.DEFAULTS["cloud_model"], cfg


def test_unknown_key_is_a_mistake_not_a_silent_none():
    try:
        cloud.model({"sufler": {}}, "cloud_imaginary_model")
    except KeyError:
        return
    raise AssertionError("неизвестный ключ модели должен падать, а не возвращать что-то")


def test_no_model_literals_left_in_call_sites():
    """Литерал в точке выхода — это будущее расхождение.

    Сторож смотрит src/ и scripts/: ночные скрипты ходят в облако теми же
    дорогами, что демон, и раньше именно они разошлись с примером конфига.
    """
    offenders = []
    for root in (REPO / "src", REPO / "scripts"):
        for path in sorted(root.glob("*.py")):
            if path.name == "cloud.py":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue        # в комментариях имя модели упомянуть можно
                for m in _LITERAL.finditer(line):
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {m.group(1)}")
    assert not offenders, (
        "имя модели зашито в точке вызова, дефолты снова разъедутся:\n  "
        + "\n  ".join(offenders))
