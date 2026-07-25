"""Ночная джоба обязана сообщать launchd, что она упала.

scripts/nightly.sh стоит под launchd ru.charoit.tier3 в 04:15 и делает две
вещи с графом: ревизию ядер и утренний бриф. Сейчас в скрипте `set -uo
pipefail` без `-e`, а последняя команда — echo. Значит любой упавший шаг
даёт exit 0: launchd видит успешный прогон, лог никто не читает, и «бриф не
собирается уже неделю» обнаруживается по отсутствию _Сегодня.md.

Требования, зафиксированные тестами:
  1) упал шаг работы с графом → код возврата ненулевой;
  2) упавший шаг не отменяет остальные — шаги независимы, и половина
     ночной работы лучше, чем ничего;
  3) просевший бенч памяти джобу не валит: это сигнал деградации, а не
     авария (так решено в самом скрипте, комментарий там же).

Питон подменяется заглушкой: тест проверяет проводку кодов возврата,
а не работу tier3 и брифа.
"""
import pathlib
import shutil
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent
NIGHTLY = REPO / "scripts" / "nightly.sh"

FAIL_ALL = "#!/bin/sh\nexit 3\n"
FAIL_ONLY_BENCH = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *memory_bench*) exit 1 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)
FAIL_ONLY_TIER3 = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *tier3_cores*) exit 3 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)
ALL_OK = "#!/bin/sh\nexit 0\n"


def _run(tmp_path: pathlib.Path, stub: str) -> subprocess.CompletedProcess:
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(NIGHTLY, tmp_path / "scripts" / "nightly.sh")
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text(stub)
    py.chmod(0o755)
    return subprocess.run(["bash", str(tmp_path / "scripts" / "nightly.sh")],
                          capture_output=True, text=True, timeout=60)


def test_script_is_syntactically_valid():
    assert subprocess.run(["bash", "-n", str(NIGHTLY)]).returncode == 0


def test_failed_step_is_reported_to_launchd(tmp_path):
    r = _run(tmp_path, FAIL_ONLY_TIER3)
    assert r.returncode != 0, "упавшая ревизия ядер отдала exit 0 — джоба «успешна»"


def test_failed_step_does_not_cancel_the_rest(tmp_path):
    """Шаги независимы: упавшая ревизия не отменяет утренний бриф."""
    r = _run(tmp_path, FAIL_ONLY_TIER3)
    assert "morning brief" in r.stdout, r.stdout


def test_sagging_bench_is_a_warning_not_a_failure(tmp_path):
    """Так решено в самом скрипте — тест закрепляет решение, а не меняет его."""
    r = _run(tmp_path, FAIL_ONLY_BENCH)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "БЕНЧ" in r.stdout


def test_clean_run_is_clean(tmp_path):
    r = _run(tmp_path, ALL_OK)
    assert r.returncode == 0, r.stdout + r.stderr


def test_everything_broken_is_reported(tmp_path):
    r = _run(tmp_path, FAIL_ALL)
    assert r.returncode != 0
