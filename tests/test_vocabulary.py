"""Словарь замен: границы слов, регистр, пустой конфиг."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from vocabulary import apply, compile_rules  # noqa: E402


def test_word_boundaries_and_case():
    rules = compile_rules({"sufler": {"vocabulary": {
        "чароид": "Чароит", "юпэй": "YuPay"}}})
    out = apply("про чароид и ЮПЭЙ; чароидный не трогаем", rules)
    assert "Чароит и YuPay" in out
    assert "чароидный" in out, "часть слова заменяться не должна"


def test_empty_vocab_is_noop():
    assert compile_rules({"sufler": {}}) == []
    assert apply("текст", []) == "текст"
