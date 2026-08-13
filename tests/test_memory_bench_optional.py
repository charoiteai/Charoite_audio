"""Ненастроенный бенч памяти — не провал бенча.

`scripts/memory_bench.py` выходил с ошибкой, если рядом нет
`config/memory_bench.yaml`. Ночная джоба ловила ненулевой код и печатала
«⚠️ БЕНЧ ПАМЯТИ ПРОСЕЛ — смотри выше» — каждую ночь у каждого, кто бенч не
заводил. Предупреждение, которое горит всегда, приучает не смотреть на
предупреждения; той же болезнью болел CI до аудита 0.46.0.

Различаем два состояния: «не настроен» (подсказка и выход по нулю) и
«настроен, но ответы не сошлись» (это и есть просевший бенч).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "memory_bench.py"), *args],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)


def test_missing_bench_file_is_not_a_failure():
    if (ROOT / "config" / "memory_bench.yaml").exists():
        # На машине с настроенным бенчом проверять нечего: этот тест про то,
        # как ведёт себя ЧУЖАЯ установка, где файла нет.
        return
    r = _run([])
    assert r.returncode == 0, f"ненастроенный бенч отдал код {r.returncode}: {r.stdout}{r.stderr}"


def test_it_says_how_to_turn_the_bench_on():
    if (ROOT / "config" / "memory_bench.yaml").exists():
        return
    out = _run([]).stdout
    assert "memory_bench.example.yaml" in out, \
        "молчаливый пропуск: человеку не сказано, как включить бенч"


def test_example_file_exists_to_copy():
    # Подсказка бесполезна, если копировать нечего.
    assert (ROOT / "config" / "memory_bench.example.yaml").exists()
