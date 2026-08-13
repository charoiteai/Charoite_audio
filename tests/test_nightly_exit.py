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
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
NIGHTLY = REPO / "scripts" / "nightly.sh"

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import graphs  # noqa: E402
import morning_brief  # noqa: E402
import tier3_cores  # noqa: E402

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
# Модель молчала: скрипт досье отработал, но темы остались без разбора.
DOSSIER_WITHOUT_MODEL = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *nightly_dossier.py*) exit 2 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)
# Заглушка, подглядывающая в статус прямо во время прогона.
PEEK_STATUS = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *tier3_cores*) cat logs/nightly.json ;;\n"
    "esac\n"
    "exit 0\n"
)


def _status(tmp_path):
    import json
    return json.loads((tmp_path / "logs" / "nightly.json").read_text())


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
    """Шаги независимы: упавшая ревизия не отменяет утренний бриф.

    Проверяем именно ПОСЛЕДНИЙ бриф: с 13.08 их два, и ранний идёт до
    ревизии — по одному вхождению строки нельзя понять, дожил ли прогон до
    конца.
    """
    r = _run(tmp_path, FAIL_ONLY_TIER3)
    assert r.stdout.rindex("morning brief") > r.stdout.index("tier3 cores"), r.stdout


def test_brief_is_written_before_the_heavy_steps(tmp_path):
    """Бриф пишется дважды, и первый раз — до ревизии ядер.

    Ревизия на большом графе идёт часами (13.08 — пять с лишним), а бриф
    стоит секунды и модель не зовёт. Когда он был только последним шагом,
    ночь без брифа означала, что человек утром читает вчерашний файл.
    """
    r = _run(tmp_path, ALL_OK)
    assert r.stdout.index("morning brief") < r.stdout.index("tier3 cores"), r.stdout


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


@pytest.mark.parametrize("script, argv, marker", [
    (tier3_cores, ["tier3_cores.py", "--all-graphs", "--apply"], "Ядра"),
    (morning_brief, ["morning_brief.py"], "Встречи-архив"),
])
def test_missing_vault_is_not_a_failure(script, argv, marker, tmp_path,
                                        monkeypatch, capsys):
    """«Обходить нечего» — не авария; ровно этим ходят оба шага джобы.

    Графы оба скрипта искали в одной захардкоженной папке
    (~/Library/.../iCloud~md~obsidian/Documents): ревизия делала sys.exit со
    строкой, бриф — iterdir() по несуществующему пути, то есть traceback.
    Оба дают ненулевой код. Пока падение шага тонуло в логе, это было
    незаметно; теперь оно красит прогон, и у любого, кто держит Obsidian не
    в iCloud, launchd будет краснеть каждую ночь без единой причины.
    """
    monkeypatch.setattr(graphs, "ICLOUD", tmp_path / "нет-iCloud")
    monkeypatch.setattr(graphs, "configured_graph", lambda: None)
    monkeypatch.setattr(sys, "argv", argv)
    script.main()
    assert marker in capsys.readouterr().out, "молча вышел — человеку нечего понять"


def test_vault_is_the_folder_above_the_configured_graph(tmp_path, monkeypatch):
    """Граф ищется рядом с настроенным graph_dir, а не только в iCloud.

    Vault — это папка НАД графом: sufler.graph_dir указывает на ~/Vault/Работа,
    а ночью надо обойти и ~/Vault/Личное. Одного захардкоженного пути мало.
    """
    monkeypatch.setattr(graphs, "ICLOUD", tmp_path / "нет-iCloud")
    work, home = tmp_path / "Vault" / "Работа", tmp_path / "Vault" / "Личное"
    for g in (work, home):
        (g / "Ядра").mkdir(parents=True)
    monkeypatch.setattr(graphs, "configured_graph", lambda: work)
    assert graphs.all_graphs("Ядра") == [home, work], "граф из vault не найден"


def test_status_file_is_written(tmp_path):
    """Итог прогона обязан лечь рядом с данными.

    Логи launchd живут в /tmp и исчезают при перезагрузке: по ним «ночью
    ничего не делалось» неотличимо от «файл стёрся». Статус читает
    приложение и показывает на «Сегодня».
    """
    _run(tmp_path, ALL_OK)
    assert _status(tmp_path)["state"] == "ok"


def test_running_is_visible_while_the_pass_is_going(tmp_path):
    """Прогон занимает до часа с лишним — всё это время «не запускалось»
    было бы прямой ложью."""
    r = _run(tmp_path, PEEK_STATUS)
    assert '"state":"running"' in r.stdout, r.stdout


def test_silent_model_is_not_a_success(tmp_path):
    """12.08: локальный сервер лёг посреди прогона, 258 тем ушли без
    разбора — и ночь отчиталась как успешная."""
    _run(tmp_path, DOSSIER_WITHOUT_MODEL)
    s = _status(tmp_path)
    assert s["state"] == "failed", s
    assert "модель-молчала" in s["failed"], s


def test_sudden_death_leaves_a_failure_not_a_forever_running(tmp_path):
    """set -e выносит скрипт из любой необработанной команды: статус не
    должен остаться «идёт» навсегда — это поломка под видом работы."""
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(NIGHTLY, tmp_path / "scripts" / "nightly.sh")
    # Питона нет вовсе — первая же строка с $PY валит скрипт по set -e.
    r = subprocess.run(["bash", str(tmp_path / "scripts" / "nightly.sh")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert _status(tmp_path)["state"] == "failed", _status(tmp_path)
