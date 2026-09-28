"""Дверь чужого интерпретатора (№473): чистое окружение ребёнка и исход значением.

Защита проверяется в обе стороны, как у пробы готовности: сначала стенд
убеждается, что подмена на сыром окружении действительно происходит, и только
потом — что дверь её снимает. Тест, который смотрит лишь на флаг, доказывает
намерение, а не результат.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import foreign_python as fp  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402


def stub(tmp_path: pathlib.Path, body: str, name: str = "stub.py") -> pathlib.Path:
    """Скрипт-заглушка в своём каталоге: дверь зовёт его `sys.executable`."""
    d = tmp_path / "code"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


# ------------------------------------------------------------------ исходы

def test_a_json_object_on_stdout_is_ok(tmp_path):
    s = stub(tmp_path, 'import json, sys\nprint(json.dumps({"argv": sys.argv[1:]}))\n')
    out = fp.run_json(sys.executable, s, ["a", "--b", "c"], timeout=30)
    assert out == fp.Outcome(fp.OK, payload={"argv": ["a", "--b", "c"]})
    assert out.ok


def test_the_engine_unavailable_code_is_unavailable_with_the_last_stderr_line(tmp_path):
    s = stub(tmp_path, f'import sys\nprint("шум", file=sys.stderr)\n'
                       f'print("нет весов — скачать", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out == fp.Outcome(fp.UNAVAILABLE, reason="нет весов — скачать")
    assert not out.ok


def test_unavailable_without_stderr_still_says_something(tmp_path):
    s = stub(tmp_path, f"import sys\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n")
    assert fp.run_json(sys.executable, s, [], timeout=30) == fp.Outcome(
        fp.UNAVAILABLE, reason="движок недоступен")


def test_any_other_code_is_a_failure_with_the_code_and_the_last_line(tmp_path):
    s = stub(tmp_path, 'raise RuntimeError("модель упала")\n')
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.kind == fp.FAILED
    assert out.reason == "код 1: RuntimeError: модель упала"


def test_a_silent_failure_says_so(tmp_path):
    s = stub(tmp_path, "import sys\nsys.exit(3)\n")
    assert fp.run_json(sys.executable, s, [], timeout=30) == fp.Outcome(
        fp.FAILED, reason="код 3: без вывода")


@pytest.mark.parametrize("printed,why", [
    ("", "ответ не JSON"),
    ("не json", "ответ не JSON"),
    ('[1, 2]', "ответ не JSON-объект"),
    ('"строка"', "ответ не JSON-объект"),
])
def test_stdout_that_is_not_a_json_object_is_a_failure(tmp_path, printed, why):
    s = stub(tmp_path, f"import sys\nsys.stdout.write({printed!r})\n")
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.kind == fp.FAILED and out.reason.startswith(why)


def test_broken_utf8_on_stdout_is_a_failure_not_a_crash(tmp_path):
    s = stub(tmp_path, "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe{}')\n")
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.kind == fp.FAILED and out.reason.startswith("ответ не JSON")


def test_the_ceiling_kills_the_child_and_says_how_long(tmp_path):
    s = stub(tmp_path, "import time\ntime.sleep(30)\n")
    assert fp.run_json(sys.executable, s, [], timeout=1.0) == fp.Outcome(
        fp.FAILED, reason="не уложился в 1 с")


def test_a_missing_interpreter_is_a_failure(tmp_path):
    s = stub(tmp_path, "print('{}')\n")
    out = fp.run_json(tmp_path / "нет" / "python", s, [], timeout=30)
    assert out == fp.Outcome(fp.FAILED, reason=f"нет интерпретатора {tmp_path / 'нет' / 'python'}")


def test_an_interpreter_that_cannot_start_is_a_failure(tmp_path):
    s = stub(tmp_path, "print('{}')\n")
    not_exec = tmp_path / "python"
    not_exec.write_text("#!/bin/sh\n", encoding="utf-8")
    not_exec.chmod(0o600)
    out = fp.run_json(not_exec, s, [], timeout=30)
    assert out.kind == fp.FAILED and out.reason.startswith("не запустился:")


def test_a_path_with_a_null_byte_is_a_failure_not_an_exception(tmp_path):
    s = stub(tmp_path, "print('{}')\n")
    out = fp.run_json("/usr/bin/python3\x00", s, [], timeout=30)
    assert out.kind == fp.FAILED and out.reason.startswith("не запустился:")


def test_stdin_is_closed_so_a_reader_does_not_hang(tmp_path):
    s = stub(tmp_path, 'import json, sys\nprint(json.dumps({"stdin": sys.stdin.read()}))\n')
    assert fp.run_json(sys.executable, s, [], timeout=30).payload == {"stdin": ""}


# ------------------------------------------------------------- окружение

def test_clean_env_applies_every_row_and_keeps_the_rest():
    base = {k: "родитель" for k in fp.ISOLATION} | {"HOME": "/home/u", "LANG": "ru_RU.UTF-8"}
    env = fp.clean_env(base)
    for key, value in fp.ISOLATION.items():
        if value is None:
            assert key not in env, key
        else:
            assert env[key] == value, key
    assert env["HOME"] == "/home/u" and env["LANG"] == "ru_RU.UTF-8"
    assert base["PYTHONPATH"] == "родитель", "вход не меняется"


def test_the_child_sees_the_clean_environment_and_runs_in_the_script_folder(tmp_path, monkeypatch):
    for key in fp.ISOLATION:
        monkeypatch.setenv(key, "родитель")
    s = stub(tmp_path, "import json, os\nprint(json.dumps({'env': dict(os.environ), 'cwd': os.getcwd()}))\n")
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.ok, out.reason
    env = out.payload["env"]
    for key, value in fp.ISOLATION.items():
        if value is None:
            assert key not in env, key
        else:
            assert env[key] == value, key
    assert pathlib.Path(out.payload["cwd"]).resolve() == s.parent.resolve()


def hijack_rig(tmp_path):
    """Каталог с поддельным json.py и скрипт, который импортирует json."""
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "json.py").write_text("raise SystemExit('подмена json исполнилась')\n", encoding="utf-8")
    s = stub(tmp_path, 'import json\nprint(json.dumps({"json": json.__file__}))\n')
    return evil, s


def test_pythonpath_of_the_parent_does_not_reach_the_child(tmp_path, monkeypatch):
    evil, s = hijack_rig(tmp_path)
    raw = subprocess.run([sys.executable, str(s)], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(evil)})
    assert "подмена json исполнилась" in raw.stderr, "стенд не воспроизвёл подмену — тест пустой"
    monkeypatch.setenv("PYTHONPATH", str(evil))
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.ok, out.reason
    assert not out.payload["json"].startswith(str(evil))


def test_a_module_planted_next_to_the_script_is_not_imported(tmp_path):
    """Каталог скрипта (он же рабочий) не попадает в sys.path: SAFEPATH."""
    s = stub(tmp_path, 'import json\nprint(json.dumps({"json": json.__file__}))\n')
    (s.parent / "json.py").write_text("raise SystemExit('подмена json исполнилась')\n", encoding="utf-8")
    raw = subprocess.run([sys.executable, str(s)], capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "PYTHONSAFEPATH"})
    assert "подмена json исполнилась" in raw.stderr, "стенд не воспроизвёл подмену — тест пустой"
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.ok, out.reason


def test_pythonhome_of_the_parent_does_not_break_the_child(tmp_path, monkeypatch):
    s = stub(tmp_path, 'import json\nprint(json.dumps({}))\n')
    bogus = str(tmp_path / "нет-библиотеки")
    raw = subprocess.run([sys.executable, str(s)], capture_output=True, text=True,
                         env={**os.environ, "PYTHONHOME": bogus})
    assert raw.returncode != 0, "стенд не сломал интерпретатор — тест пустой"
    monkeypatch.setenv("PYTHONHOME", bogus)
    assert fp.run_json(sys.executable, s, [], timeout=30).ok


def test_the_child_writes_no_bytecode_next_to_the_code(tmp_path):
    s = stub(tmp_path, 'import sys, pathlib, json\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n'
                       'import helper\nprint(json.dumps({"v": helper.V}))\n')
    (s.parent / "helper.py").write_text("V = 1\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_s = raw_dir / "stub.py"
    raw_s.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    (raw_dir / "helper.py").write_text("V = 1\n", encoding="utf-8")
    subprocess.run([sys.executable, str(raw_s)], capture_output=True,
                   env={k: v for k, v in os.environ.items()
                        if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")})
    assert (raw_dir / "__pycache__").exists(), "стенд не записал байткод — тест пустой"
    out = fp.run_json(sys.executable, s, [], timeout=30)
    assert out.payload == {"v": 1}
    assert not (s.parent / "__pycache__").exists()


def test_stderr_is_read_as_utf8_whatever_the_parent_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    s = stub(tmp_path, f'import sys\nprint("нет пакета mlx-audio", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    assert fp.run_json(sys.executable, s, [], timeout=30).reason == "нет пакета mlx-audio"


def test_warnings_as_errors_of_the_parent_do_not_kill_the_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONWARNINGS", "error")
    s = stub(tmp_path, 'import json, warnings\nwarnings.warn("устарело", DeprecationWarning)\n'
                       'print(json.dumps({}))\n')
    assert fp.run_json(sys.executable, s, [], timeout=30).ok


def test_outcome_kinds_are_strings_not_tuple_fields():
    """`ClassVar` в NamedTuple становится полем (№477) — виды исхода живут в модуле."""
    assert fp.Outcome._fields == ("kind", "payload", "reason")
    assert (fp.OK, fp.UNAVAILABLE, fp.FAILED) == ("ok", "unavailable", "failed")
    assert json.loads(json.dumps(fp.Outcome(fp.FAILED, reason="x")._asdict()))["kind"] == "failed"
