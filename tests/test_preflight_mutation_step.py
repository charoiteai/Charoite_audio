"""Шаг 4 `scripts/preflight.sh`: код 3 мутатора — пропуск только для занятости
на старте; невзятый замок с тем же кодом — провал, а `PREFLIGHT_FORCE` до
мутатора не доходит (№595: Sonnet I-1 = DS I1, Haiku I-2 проверки правок Opus).

Preflight гоняется в подставном дереве: настоящие `preflight.sh` и
`src/exit_codes.py`, остальное — заглушки, которые отвечают «чисто»."""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

MUTATOR = """import os, pathlib, sys
pathlib.Path(os.environ["FAKE_ARGV"]).write_text(" ".join(sys.argv[1:]))
print(os.environ["FAKE_MSG"])
sys.exit(int(os.environ["FAKE_RC"]))
"""


@pytest.fixture
def tree(tmp_path):
    t = tmp_path / "tree"
    (t / "scripts").mkdir(parents=True)
    (t / "src").mkdir()
    (t / "tests").mkdir()   # шаг 1 линтует src/ scripts/ tests/
    (t / ".github" / "workflows").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "preflight.sh", t / "scripts" / "preflight.sh")
    shutil.copy(ROOT / "src" / "exit_codes.py", t / "src" / "exit_codes.py")
    (t / "src" / "busy_signals.py").write_text("def machine_busy(root, **k):\n    return []\n")
    (t / ".github" / "workflows" / "ci.yml").write_text("env:\n  RUFF_VERSION: '0.0.0'\n")
    for name in ("layout_map.py", "check_private_markers.py"):
        (t / "scripts" / name).write_text("")
    (t / "scripts" / "mutate_check.py").write_text(MUTATOR)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", *cmd], cwd=t, env=env, check=True)
    return t


def _preflight(tree, tmp_path, rc, msg, **extra):
    argv = tmp_path / "argv"
    # PATH без pipx/uvx: ruff версии 0.0.0 не встанет — шаг уходит в пропуски сам
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PREFLIGHT_PY": sys.executable,
           "PREFLIGHT_SKIP": "pytest,swift", "CHAROITE_ROOT": str(tmp_path),
           "FAKE_ARGV": str(argv), "FAKE_MSG": msg, "FAKE_RC": str(rc), **extra}
    r = subprocess.run(["/bin/bash", "scripts/preflight.sh", "HEAD"], cwd=tree, env=env,
                       capture_output=True, text=True, timeout=60)
    # мутатор не запущен (ранний выход preflight) — пустой argv, а не голый
    # FileNotFoundError без вывода скрипта (Sonnet M1 узкого круга №595)
    return r, argv.read_text() if argv.exists() else ""


def test_no_temp_dir_is_a_named_refusal(tree, tmp_path):
    """Временный каталог не создаётся — отказ старта кодом 2 с причиной, а не
    логи шагов в «/» и FAIL без слов (Sonnet I3 = DS I1 узкого круга №595)."""
    r, argv = _preflight(tree, tmp_path, 0, "ok", TMPDIR=str(tmp_path / "нет"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "временный каталог не создаётся" in r.stdout and argv == ""


def test_busy_start_is_a_skip(tree, tmp_path):
    r, _ = _preflight(tree, tmp_path, 3, "машина занята (живая встреча) — мутатор не стартует (--force, чтобы настоять)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "mutation(машина занята)" in r.stdout and "FAIL" not in r.stdout


def test_lock_refused_is_a_failure(tree, tmp_path):
    """Тот же код 3 у невзятого замка: ФС без flock ожиданием не лечится."""
    r, _ = _preflight(tree, tmp_path, 3, "замок мутатора не взят (ретраи против чужой пробы кончились "
                                         "или ФС без flock) — повтори запуск")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL: mutation" in r.stdout and "машина занята" not in r.stdout


def test_force_does_not_reach_the_mutator(tree, tmp_path):
    """`--force` мутатора снимает уступку встрече и ночи между мутантами — согласие
    на старт preflight его не даёт."""
    r, argv = _preflight(tree, tmp_path, 0, "ok", PREFLIGHT_FORCE="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--range" in argv and "--force" not in argv
