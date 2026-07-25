"""config.example.yaml — единственная документация по ключам конфига.

Ключ, который читает код, но которого нет в примере, не существует для
пользователя: узнать о нём можно только чтением исходников. Так вышло с
sufler.language — ради него сделаны три релиза интернационализации (0.20.0,
0.21.0, 0.22.0), английские промпты, английские значения полей графа и
демо-граф graph_en, а включить его нельзя, не заглянув в src/llm.py.
Хуже: в примере есть stt.language — ЯЗЫК РАСПОЗНАВАНИЯ, другой ключ в
другой секции. Пользователь ставит его и получает не то.

Тест ловит расхождение целиком, а не один этот случай: собирает ключи
sufler.*, которые код реально читает, и требует, чтобы каждый был либо в
примере, либо в списке исключений ниже — с причиной.
"""
import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config" / "config.example.yaml"
CI = REPO / ".github" / "workflows" / "ci.yml"

# Ключи, которых сознательно нет в примере, — каждый с причиной.
UNDOCUMENTED_ON_PURPOSE = {
    # шаблон минуток показан закомментированным блоком: значение
    # многострочное, «пустой ключ» тут выглядел бы как поломка
    "minutes_template",
    # sufler.model читает только dictate_note.py, всё остальное берёт
    # llm.model. Ключ-дубль: пока не решено, оставлять его или свести к
    # llm.model, рекламировать в примере нечего
    "model",
}

READ_PATTERNS = (
    re.compile(r'\["sufler"\]\.get\(\s*"([a-z0-9_]+)"'),
    re.compile(r'\.get\("sufler",\s*\{\}\)\.get\(\s*"([a-z0-9_]+)"'),
)


def _keys_read_by_code() -> set[str]:
    keys: set[str] = set()
    for path in sorted((REPO / "src").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in READ_PATTERNS:
            keys.update(pattern.findall(text))
    return keys


def _example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_example_is_valid_yaml():
    assert isinstance(_example(), dict)


def test_language_key_is_documented():
    """Тот самый ключ, ради которого сделана вся интернационализация."""
    assert "language" in _example()["sufler"], \
        "sufler.language не описан в примере — включить английский можно только из исходников"


def test_stt_language_is_a_different_key():
    """Защита от путаницы: два language в конфиге, и это нормально — но оба
    должны быть на месте, иначе один читается вместо другого."""
    cfg = _example()
    assert "language" in cfg["stt"], "stt.language — язык распознавания"
    assert "language" in cfg["sufler"], "sufler.language — язык документов встречи"


def test_every_key_the_code_reads_is_documented():
    missing = sorted(_keys_read_by_code() - set(_example()["sufler"]) - UNDOCUMENTED_ON_PURPOSE)
    assert not missing, (
        "код читает ключи, которых нет в config.example.yaml: "
        + ", ".join(f"sufler.{k}" for k in missing)
        + " — опишите их в примере или внесите в UNDOCUMENTED_ON_PURPOSE с причиной")


def test_ci_checks_the_same_required_keys():
    """CI проверяет обязательные ключи своим списком — он не должен отставать."""
    text = CI.read_text(encoding="utf-8")
    for path in ("sufler.user_name", "sufler.language"):
        assert f'"{path}"' in text, f"ci.yml не проверяет {path}"
