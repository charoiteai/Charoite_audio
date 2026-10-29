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
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
NIGHTLY = REPO / "scripts" / "nightly.sh"
# Интерпретатор — тот, которым ночь зовёт launchd (агент из настроек
# приложения: ProgramArguments = /bin/bash), а не первый bash в PATH. На macOS
# это bash 3.2, у разработчика в PATH — brew-овский 5.x: под 5.x пустой массив
# при set -u раскрывается молча, под 3.2 — «unbound variable», и чистая ночь
# две ночи подряд писалась как failed, пока тесты зеленели (23.09, №359).
LAUNCHD_BASH = "/bin/bash"

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


# Заглушка python, у которой monotonic стоит на месте: так выглядит ночь,
# которую машина проспала (wall-clock ушёл, monotonic — нет).
FROZEN_CLOCK = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  -c) echo 1000; exit 0 ;;\n"
    "  *) sleep 1; exit 0 ;;\n"      # шаг длится секунду: потолок NIGHTLY_MAX_H=0 успевает выйти
    "esac\n"
)
SLOW_OK = "#!/bin/sh\nsleep 1\nexit 0\n"


def _run(tmp_path: pathlib.Path, stub: str, **env_extra: str) -> subprocess.CompletedProcess:
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(NIGHTLY, tmp_path / "scripts" / "nightly.sh")
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text(stub)
    py.chmod(0o755)
    # Корень данных для этой установки — сам tmp_path, и сказать об этом надо
    # ЯВНО: обвязка прогона называет канону свой временный корень и уводит его
    # детям через `CHAROITE_ROOT`, а у скопированной сюда установки корень
    # другой. Молчание означало бы, что ночной статус ляжет в чужую папку.
    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    env.pop("CHAROITE_SLOT", None)
    env.pop("NIGHT_GATE", None)
    for name in ("DOSSIER_BUDGET", "DOSSIER_FULL_BUDGET", "DOSSIER_SUNDAY_BUDGET", "NIGHTLY_DOSSIER_FULL"):
        env.pop(name, None)       # окружение разработчика не решает за тест (выход out1 №749, DS M4)
    env.update(env_extra)
    return subprocess.run([LAUNCHD_BASH, str(tmp_path / "scripts" / "nightly.sh")],
                          capture_output=True, text=True, timeout=60, env=env)


# CLI облака не запускается (обновление под ногами): ревизия досье — код 3.
DOSSIER_REVIEW_CLI_DOWN = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *nightly_dossier_review.py*) exit 3 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)
CORES_CLI_DOWN = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *nightly_claude_cores.py*) exit 3 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)
DOSSIER_REVIEW_PARTIAL = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *nightly_dossier_review.py*) exit 2 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)


def test_cloud_cli_down_marks_the_night_failed(tmp_path):
    """Ночь 05.09: обновление Claude CLI уронило семь тем ревизии, а ночь
    отчиталась «ok» (аудит GLM/DS). Код 3 у облачного шага — состояние
    failed в статусе (его читает утренний бриф); launchd-код остаётся 0:
    шаг необязательный и самовосстанавливается (критика GLM/DS по #499)."""
    for stub in (DOSSIER_REVIEW_CLI_DOWN, CORES_CLI_DOWN):
        r = _run(tmp_path, stub)
        assert r.returncode == 0, r.stdout
        assert "CLI облака не отвечает" in r.stdout, r.stdout
        assert _status(tmp_path)["state"] == "failed", _status(tmp_path)
        assert "(cli)" in str(_status(tmp_path).get("failed", "")), _status(tmp_path)


def _stub_exit(script: str, code: int) -> str:
    return ("#!/bin/sh\n"
            'case "$1" in\n'
            f"  *{script}*) exit {code} ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n")


@pytest.mark.parametrize("script,code,label,said", [
    ("nightly_claude_cores.py", 11, "claude-cores(провайдер)", "провайдер ночи не принят"),
    ("nightly_claude_cores.py", 14, "claude-cores(аттестация)", "аттестация провайдера не прошла"),
    ("nightly_claude_cores.py", 15, "claude-cores(недоступен)", "провайдер ночи недоступен"),
    ("nightly_dossier_review.py", 11, "ревизия-досье(провайдер)", "провайдер ночи не принят"),
    ("nightly_dossier_review.py", 16, "ревизия-досье(этап2)", "досье — этап 2"),
])
def test_night_provider_outcomes_have_their_own_labels(tmp_path, script, code, label, said):
    """Отказ политики, подмена, недоступность и пропуск этапа — разные утра:
    у каждого кода своя метка в статусе ночи (№792, in3 S-I2, DS-I6, S-I6).
    Код 3 (`(cli)`) остаётся за CLI Claude — тест выше."""
    r = _run(tmp_path, _stub_exit(script, code))
    assert r.returncode == 0, r.stdout + r.stderr
    assert said in r.stdout, r.stdout
    failed = str(_status(tmp_path).get("failed", ""))
    assert label in failed, failed
    assert "(cli)" not in failed, failed


def test_stage_pending_alone_does_not_turn_the_night_red(tmp_path):
    """Пропуск этапа 2 (код 16) — плановый: одна такая метка оставляет ночь ok,
    иначе под tinfoil красна каждая ночь и 14 в ней не видна (opus, критика 1).
    С настоящим сбоем рядом — failed, как прежде."""
    r = _run(tmp_path, _stub_exit("nightly_dossier_review.py", 16))
    assert r.returncode == 0, r.stdout + r.stderr
    status = _status(tmp_path)
    assert status["state"] == "ok" and "ревизия-досье(этап2)" in status["failed"], status
    stub = ("#!/bin/sh\n"
            'case "$1" in\n'
            "  *nightly_dossier_review*) exit 16 ;;\n"
            "  *nightly_claude_cores*) exit 14 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n")
    _run(tmp_path, stub)
    status = _status(tmp_path)
    assert status["state"] == "failed" and "claude-cores(аттестация)" in status["failed"], status


def test_partially_failed_review_is_visible_but_not_fatal(tmp_path):
    """Часть тем не проверена — предупреждение в FAILED, ночь не авария."""
    r = _run(tmp_path, DOSSIER_REVIEW_PARTIAL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "часть тем не проверена" in r.stdout, r.stdout
    assert "ревизия-досье(сбои)" in str(_status(tmp_path).get("failed", "")), _status(tmp_path)


def test_graph_listing_survives_a_transient_eintr(tmp_path, monkeypatch):
    """iCloud-каталог отвечал EINTR посреди ночи (17.08) и ронял шаг целиком —
    один повтор спасает, устойчивый отказ — пропуск корня вслух."""
    for n in graphs.ENV_GRAPH_NAMES:      # конфиг-ветка roots(), не env (GLM I1 по #525)
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(graphs, "ICLOUD", tmp_path / "нет-iCloud")
    work = tmp_path / "Vault" / "Работа"
    (work / "Ядра").mkdir(parents=True)
    monkeypatch.setattr(graphs, "configured_graph", lambda: work)
    real = pathlib.Path.iterdir
    hits = {"n": 0}

    def flaky(self):
        hits["n"] += 1
        if hits["n"] == 1:
            raise InterruptedError(4, "Interrupted system call")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", flaky)
    monkeypatch.setattr(graphs.time, "sleep", lambda _s: None)
    assert graphs.all_graphs("Ядра") == [work]

    def broken(self):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(pathlib.Path, "iterdir", broken)
    assert graphs.all_graphs("Ядра") == [], "устойчивый отказ — пустой список, не traceback"


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
    assert "бенч памяти (answer) не отработал" in r.stdout
    assert _status(tmp_path)["state"] == "ok", _status(tmp_path)


# Заглушка, которая пишет, чем её звали: порядок шагов и флаги бенча.
LOG_ARGV = '#!/bin/sh\necho "$@" >> logs/argv.log\nexit 0\n'


def test_bench_runs_three_profiles_without_model_before_the_last_brief(tmp_path):
    """№629 ч. 2: ночь мерит выдачу трёх профилей демона (`--stats`, без модели),
    пишет итог в базу и делает это ДО последнего брифа — бриф показывает тревогу."""
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    r = _run(tmp_path, LOG_ARGV)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (tmp_path / "logs" / "argv.log").read_text(encoding="utf-8").splitlines()
    bench = [i for i, c in enumerate(calls) if "memory_bench" in c]
    assert [calls[i].split("memory_bench.py ", 1)[1] for i in bench] == [
        f"--stats --profile {p} --record" for p in ("answer", "live", "expand")]
    last_brief = max(i for i, c in enumerate(calls) if "morning_brief" in c)
    assert max(bench) < last_brief, calls


TIER3_STOPPED = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "  *tier3_cores*) exit 4 ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n"
)


def test_revision_cut_by_the_ceiling_is_not_an_ok_night(tmp_path):
    """Код 4 ревизии ядер — «потолок оборвал»: ночь не «ok», но и не авария
    (№358: раньше оборванный прогон считался успехом)."""
    r = _run(tmp_path, TIER3_STOPPED)
    assert r.returncode == 0, r.stdout + r.stderr
    status = _status(tmp_path)
    assert status["state"] == "failed" and "ревизия-ядер(поздно)" in status["failed"], status


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
    for n in graphs.ENV_GRAPH_NAMES:      # конфиг-ветка roots(), не env (GLM I1 по #525)
        monkeypatch.delenv(n, raising=False)
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
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, CHAROITE_ROOT=str(tmp_path)))
    assert r.returncode != 0
    assert _status(tmp_path)["state"] == "failed", _status(tmp_path)


def test_night_slept_through_is_reported_as_sleep_not_failure(tmp_path):
    """30.08: прогон 04:20–09:59 на спящем Mac — четыре «(поздно)» и «failed»,
    хотя не упало ничего. Проспанное время = wall-clock минус monotonic;
    только «(поздно)» при сне — состояние «slept» (аудит 30.08, GLM I3)."""
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(NIGHTLY, tmp_path / "scripts" / "nightly.sh")
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text(FROZEN_CLOCK)
    py.chmod(0o755)
    env = dict(os.environ, NIGHTLY_MAX_H="0", CHAROITE_NIGHTLY_SLEEP_S="0",
               CHAROITE_ROOT=str(tmp_path))
    r = subprocess.run(["bash", str(tmp_path / "scripts" / "nightly.sh")],
                       capture_output=True, text=True, timeout=60, env=env)
    st = _status(tmp_path)
    assert st["state"] == "slept", (st, r.stdout)
    assert "(поздно)" in st["failed"] and st["slept_s"] >= 1
    assert "спала" in r.stdout
    # без сна те же «(поздно)» остаются поломкой
    py.write_text(SLOW_OK)
    r = subprocess.run(["bash", str(tmp_path / "scripts" / "nightly.sh")],
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, NIGHTLY_MAX_H="0",
                                CHAROITE_ROOT=str(tmp_path)))
    assert _status(tmp_path)["state"] == "failed", (_status(tmp_path), r.stdout)


def _gate(tmp_path, body: str) -> str:
    """Шов NIGHT_GATE: заглушка гейта, которая отмечает, что её звали."""
    gate = tmp_path / "gate.sh"
    gate.write_text(f"#!/bin/sh\necho called >> {tmp_path}/gate.calls\n{body}\n")
    gate.chmod(0o755)
    return str(gate)


def _gate_record(tmp_path):
    import json
    return json.loads((tmp_path / "logs" / "nightly_gate.json").read_text())


def test_manual_run_does_not_ask_the_gate(tmp_path):
    """Гейт видят только слоты приложения (CHAROITE_SLOT=1); ручной запуск и
    чужие агенты идут как раньше."""
    _run(tmp_path, ALL_OK, NIGHT_GATE=_gate(tmp_path, "echo skip"))
    assert not (tmp_path / "gate.calls").exists()
    assert _status(tmp_path)["state"] == "ok"


@pytest.mark.parametrize("decision", ["skip", "defer battery 20%"])
def test_skipped_slot_leaves_the_night_status_alone(tmp_path, decision):
    """skip и defer выходят с кодом 0 до «идёт»: прежний итог ночи не трогается."""
    (tmp_path / "logs").mkdir()
    before = '{"started":"x","finished":"x","state":"ok","rc":0,"failed":"","slept_s":0}\n'
    (tmp_path / "logs" / "nightly.json").write_text(before)
    r = _run(tmp_path, FAIL_ALL, CHAROITE_SLOT="1",
             NIGHT_GATE=_gate(tmp_path, f"echo '{decision}'"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "logs" / "nightly.json").read_text() == before
    assert _gate_record(tmp_path)["decision"] == decision
    assert _gate_record(tmp_path)["rc"] == 0


def test_broken_gate_means_go_and_its_code_is_kept(tmp_path):
    """Сбой гейта не гасит ночь: лишний прогон дешевле пропущенной ночи.
    Код гейта остаётся в nightly_gate.json — иначе сбой не увидеть."""
    r = _run(tmp_path, ALL_OK, CHAROITE_SLOT="1", NIGHT_GATE=_gate(tmp_path, "exit 7"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "gate.calls").exists()
    rec = _gate_record(tmp_path)
    assert (rec["decision"], rec["rc"]) == ("go", 7)
    assert _status(tmp_path)["state"] == "ok"


def test_silent_gate_is_go_with_its_own_code(tmp_path):
    """Пустой вывод при коде 0 — go, но с rc 0: прежде туда попадал код
    проверки пустоты, 1, и молчание читалось как падение гейта."""
    _run(tmp_path, ALL_OK, CHAROITE_SLOT="1", NIGHT_GATE=_gate(tmp_path, "exit 0"))
    rec = _gate_record(tmp_path)
    assert (rec["decision"], rec["rc"]) == ("go", 0)


def test_go_runs_the_night(tmp_path):
    _run(tmp_path, ALL_OK, CHAROITE_SLOT="1", NIGHT_GATE=_gate(tmp_path, "echo go"))
    assert _gate_record(tmp_path)["decision"] == "go"
    assert _status(tmp_path)["state"] == "ok"


@pytest.mark.parametrize("brief_rc", [0, 1])
def test_brief_slot_only_refreshes_the_brief(tmp_path, brief_rc):
    """Утренний слот после вечерней ночи: только бриф дня — тяжёлые шаги не
    повторяются, итог ночи не трогается; код слота — код брифа."""
    (tmp_path / "logs").mkdir()
    before = '{"started":"x","finished":"x","state":"ok","rc":0,"failed":"","slept_s":0}\n'
    (tmp_path / "logs" / "nightly.json").write_text(before)
    stub = (f'#!/bin/sh\necho "$@" >> {tmp_path}/py.calls\n'
            f'case "$1" in *morning_brief.py*) exit {brief_rc} ;; esac\nexit 0\n')
    r = _run(tmp_path, stub, CHAROITE_SLOT="1", NIGHT_GATE=_gate(tmp_path, "echo brief"))
    assert r.returncode == brief_rc, r.stdout + r.stderr
    calls = (tmp_path / "py.calls").read_text().splitlines()
    assert [c for c in calls if "scripts/" in c] == ["scripts/morning_brief.py"], calls
    assert (tmp_path / "logs" / "nightly.json").read_text() == before
    assert _gate_record(tmp_path)["decision"] == "brief"


# Заглушка, записывающая аргументы шага досье.
RECORD_DOSSIER_ARGS = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    '  *nightly_dossier.py*) echo "$@" > "$CHAROITE_ROOT/dossier_args" ;;\n'
    "esac\n"
    "exit 0\n"
)


def _weekday(tmp_path: pathlib.Path, dow: str) -> str:
    """PATH с `date`, у которого `+%u` — заданный день, остальное — настоящий date."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "date"
    fake.write_text('#!/bin/sh\n[ "$1" = "+%u" ] && { echo ' + dow + '; exit 0; }\nexec /bin/date "$@"\n')
    fake.chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


def _dossier_args(tmp_path: pathlib.Path) -> list[str]:
    return (tmp_path / "dossier_args").read_text().split()


@pytest.mark.parametrize("dow, full_flag, budget, full_budget, sunday_budget, mode, expected", [
    ("7", None, None, None, None, "", "7200"),          # воскресенье: инкремент с длинным бюджетом, без --full (№809)
    ("7", None, None, None, "мусор", "", "7200"),       # свой санитайз, не будничные 2400 (вход in1 №749, DS I2)
    ("7", None, None, None, "0", "", "7200"),
    ("7", None, None, None, "5400", "", "5400"),
    ("7", None, "10800", None, None, "", "10800"),      # не короче будней: будничный бюджет владельца длиннее (№809)
    ("7", None, None, "5400", None, "", "7200"),        # бюджет ручного --full воскресенье не задаёт
    ("3", "1", None, None, None, "--full", "7200"),     # --full — только по руке, свой бюджет
    ("7", "1", None, "5400", "9000", "--full", "5400"),  # и в воскресенье по руке — бюджет ручного режима
    ("3", None, None, "5400", "9000", "", "2400"),      # будни: длинные бюджеты не путаются с будним
    ("7", None, None, None, "1800", "", "2400"),        # воскресный короче будничного — остаётся будничный
    ("3", "1", None, "мусор", None, "--full", "7200"),  # санитайз каждой переменной (выход out1 №809)
    ("3", "1", None, "0", None, "--full", "7200"),
    ("3", None, "мусор", None, None, "", "2400"),
    ("3", None, "0", None, None, "", "2400"),
    ("3", None, "00", None, None, "", "2400"),
    ("3", None, "08", None, None, "", "8"),             # десятичное, как NIGHTLY_MAX_H (выход out2 №809)
    ("3", "1", None, "007", None, "--full", "7"),
    ("7", None, None, None, "12345678", "", "12345678"),  # восемь цифр — ещё число
    ("7", None, None, None, "123456789", "", "7200"),   # девять — умолчание, не переполнение `$((10#…))`
])
def test_dossier_budget_follows_the_mode(tmp_path, dow, full_flag, budget, full_budget, sunday_budget,
                                         mode, expected):
    """№749: полная пересборка шла без бюджета до потолка ночи, хвост ночи — в
    «(поздно)». №809: воскресенье больше не включает --full — та же
    инкрементальная ночь с длинным бюджетом. Таблица «день × режим ×
    переменная → аргументы»."""
    extra = {"PATH": _weekday(tmp_path, dow)}
    for name, value in (("NIGHTLY_DOSSIER_FULL", full_flag), ("DOSSIER_BUDGET", budget),
                        ("DOSSIER_FULL_BUDGET", full_budget), ("DOSSIER_SUNDAY_BUDGET", sunday_budget)):
        if value is not None:
            extra[name] = value
    r = _run(tmp_path, RECORD_DOSSIER_ARGS, **extra)
    args = _dossier_args(tmp_path)
    assert ("--full" in args) == (mode == "--full"), args
    assert args[args.index("--budget-s") + 1] == expected, args
    if dow == "7" and not mode:   # лог называет, чей бюджет победил
        won = "(будничный не короче)" if expected == (budget or "2400") else "(воскресный)"
        assert won in r.stdout, r.stdout
