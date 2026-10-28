"""Режим `install_engine.py --verify-config-edit`: правка config.yaml кнопкой Nemotron
сверяется тем же `yaml.safe_load`, которым конфиг читает демон (№622, круг fix6)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import install_engine as ie  # noqa: E402

SCRIPT = ROOT / "scripts" / "install_engine.py"


def _payload(old: str, new: str, expected: str | None, value: str = "nemotron") -> dict:
    return {"old": old, "new": new, "key": "diarize_backend", "expected": expected, "value": value}


# Входы Critical круга n622p2-B-fix6 и текст, который из них делал построчный
# разбор приложения на 21c5614c (вывод `replacingIfUnchanged`).
CRITICAL = [
    ("S-C1 кавычка выше ключа",
     'sufler:\n  prompt: "начало\n  diarize_backend: sherpa\n  "\n  other: 1\n',
     'sufler:\n  prompt: "начало\n  diarize_backend: "nemotron"\n  "\n  other: 1\n', "sherpa"),
    ("S-C1 flow-список выше ключа",
     "sufler:\n  tags: [a,\n  diarize_backend: sherpa\n  ]\n",
     'sufler:\n  tags: [a,\n  diarize_backend: "nemotron"\n  ]\n', "sherpa"),
    ("DS-C1 кавычка до нулевого отступа",
     'sufler:\n  x: "a\n  diarize_backend: sherpa\n"\n',
     'sufler:\n  x: "a\n  diarize_backend: "nemotron"\n"\n', "sherpa"),
    ("DS-C2 вставка внутрь скаляра",
     'sufler:\n  quiet: true\n  x: "a\n  b\nother: 1"\n',
     'sufler:\n  quiet: true\n  x: "a\n  b\n  diarize_backend: "nemotron"\nother: 1"\n', None),
    ("H-C1 sufler с пробелом, продолжение",
     "sufler :\n  diarize_backend: sherpa\n    cont\n  other: 1\n",
     'sufler :\n  diarize_backend: "nemotron"\n    cont\n  other: 1\n', "sherpa"),
    ("H-C1 sufler с пробелом, flow",
     "sufler :\n  tags: [a,\n  diarize_backend: sherpa\n  ]\n",
     'sufler :\n  tags: [a,\n  diarize_backend: "nemotron"\n  ]\n', "sherpa"),
    ("H-C1 BOM",
     "﻿sufler:\n  diarize_backend: sherpa\n    cont\n  other: 1\n",
     '﻿sufler:\n  diarize_backend: "nemotron"\n    cont\n  other: 1\n', "sherpa"),
    ("H-C2 второй блок sufler",
     "sufler :\n  quiet: true\n  instant: true\n",
     'sufler :\n  quiet: true\n  instant: true\n\nsufler:\n  diarize_backend: "nemotron"\n', None),
    ("H-C3 секция flow",
     'sufler:\n  {user_name: "x", quiet: true}\nllm:\n  m: 1\n',
     'sufler:\n  {user_name: "x", quiet: true}\n  diarize_backend: "nemotron"\nllm:\n  m: 1\n', None),
    ("H-C4 заголовок в кавычке выше",
     'note: "a\nsufler:\n  diarize_backend: sherpa\n  b"\n',
     'note: "a\nsufler:\n  diarize_backend: "nemotron"\n  b"\n', "sherpa"),
]


@pytest.mark.parametrize("name,old,new,expected", CRITICAL, ids=[c[0] for c in CRITICAL])
def test_critical_inputs_refused(name, old, new, expected):
    assert ie.verify_config_edit(_payload(old, new, expected)) is not None, name


# Формы, которые пишет приложение (testFix6ФормаПриложенияПишется), — ok.
APP_FORMS = [
    ('sufler:\n  diarize_backend: "nemotron"\n  other: 1\n', 'sufler:\n  diarize_backend: "sherpa"\n  other: 1\n'),
    ("sufler:\n  diarize_backend: nemotron\n  other: 1\n", 'sufler:\n  diarize_backend: "sherpa"\n  other: 1\n'),
    ("sufler:\n  diarize_backend: 'nemotron'\n  other: 1\n", 'sufler:\n  diarize_backend: "sherpa"\n  other: 1\n'),
    ("sufler:\n  diarize_backend: nemotron  # sherpa | nemotron\n  other: 1\n",
     'sufler:\n  diarize_backend: "sherpa" # sherpa | nemotron\n  other: 1\n'),
    ("sufler:\r\n  diarize_backend: nemotron\r\n  other: 1\r\n", 'sufler:\r\n  diarize_backend: "sherpa"\r\n  other: 1\r\n'),
    ("sufler:\n  other: 1\n  diarize_backend: nemotron\nllm:\n  m: 1\n",
     'sufler:\n  other: 1\n  diarize_backend: "sherpa"\nllm:\n  m: 1\n'),
    ("sufler:\n  other: 1\n  diarize_backend: nemotron", 'sufler:\n  other: 1\n  diarize_backend: "sherpa"'),
    ("sufler:\n  diarize_backend: nemotron\n    # пояснение\n  other: 1\n",
     'sufler:\n  diarize_backend: "sherpa"\n    # пояснение\n  other: 1\n'),
]


@pytest.mark.parametrize("old,new", APP_FORMS)
def test_app_forms_pass(old, new):
    assert ie.verify_config_edit(_payload(old, new, "nemotron", value="sherpa")) is None


def test_insert_and_two_sections_pass():
    # Ключа нет — дописан в конец последней секции; ключ в двух секциях — правится последняя.
    old = "sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    quiet: true\nlog:\n  x: 1"
    new = 'sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    quiet: true\n    diarize_backend: "nemotron"\nlog:\n  x: 1'
    assert ie.verify_config_edit(_payload(old, new, None)) is None
    old = "sufler:\n  diarize_backend: sherpa\nsufler:\n  diarize_backend: sherpa"
    new = 'sufler:\n  diarize_backend: sherpa\nsufler:\n  diarize_backend: "nemotron"'
    assert ie.verify_config_edit(_payload(old, new, "sherpa")) is None


@pytest.mark.parametrize("name", ["config.example.yaml", "config.example.en.yaml", "config.example.zh.yaml"])
def test_config_examples_pass(name):
    old = (ROOT / "config" / name).read_text(encoding="utf-8")
    lines = old.split("\n")
    i = max(n for n, s in enumerate(lines) if s.strip().startswith("diarize_backend:"))
    lines[i] = '  diarize_backend: "nemotron"'
    expected = old.split("\n")[i].split(":", 1)[1].split("#")[0].strip().strip("\"'")
    assert ie.verify_config_edit(_payload(old, "\n".join(lines), expected)) is None


@pytest.mark.parametrize("old,new,expected,why", [
    ("sufler:\n  diarize_backend: sherpa\n", 'sufler:\n  diarize_backend: "nemotron"\n', "nemotron", "ожидали другое"),
    ("sufler:\n  diarize_backend: sherpa\n  quiet: true\n",
     'sufler:\n  diarize_backend: "nemotron"\n  quiet: 1\n', "sherpa", "true → 1 — другой тип"),
    ("sufler:\n  diarize_backend: sherpa\n", 'sufler:\n  diarize_backend: "sherpa"\n', "sherpa", "значение не то"),
    ("sufler:\n  diarize_backend: sherpa\nllm: 1\n", 'sufler:\n  diarize_backend: "nemotron"\n', "sherpa", "потерян ключ"),
    ("- a\n", "- a\n", None, "не словарь"),
    ("sufler: [a]\n", "sufler: [a]\n", None, "секция не словарь"),
])
def test_other_changes_refused(old, new, expected, why):
    assert ie.verify_config_edit(_payload(old, new, expected)) is not None, why


# Сверка n622p2-B-fix7 №3, №4: nan, дубль ключа, merge `<<` — правка верна, ok.
EDGE_PASS = [
    ("nan вне sufler", "x: .nan\nsufler:\n  diarize_backend: sherpa\n",
     'x: .nan\nsufler:\n  diarize_backend: "nemotron"\n'),
    ("nan внутри sufler", "sufler:\n  threshold: .nan\n  diarize_backend: sherpa\n",
     'sufler:\n  threshold: .nan\n  diarize_backend: "nemotron"\n'),
    ("дубль ключа, правка последнего", "sufler:\n  diarize_backend: sherpa\n  diarize_backend: sherpa\n",
     'sufler:\n  diarize_backend: sherpa\n  diarize_backend: "nemotron"\n'),
    ("merge и явный ключ", "b: &b\n  diarize_backend: sherpa\nsufler:\n  <<: *b\n  diarize_backend: sherpa\n",
     'b: &b\n  diarize_backend: sherpa\nsufler:\n  <<: *b\n  diarize_backend: "nemotron"\n'),
]


@pytest.mark.parametrize("name,old,new", EDGE_PASS, ids=[c[0] for c in EDGE_PASS])
def test_edge_inputs_pass(name, old, new):
    assert ie.verify_config_edit(_payload(old, new, "sherpa")) is None, name


# Сверка n622p2-B-fix7 №3, №4: nan сменили, два документа, правка не той из двух секций — отказ.
EDGE_REFUSED = [
    ("nan сменили на число", "sufler:\n  threshold: .nan\n  diarize_backend: sherpa\n",
     'sufler:\n  threshold: 1.0\n  diarize_backend: "nemotron"\n', "sherpa"),
    ("два документа", "sufler:\n  diarize_backend: sherpa\n---\na: 1\n",
     'sufler:\n  diarize_backend: "nemotron"\n---\na: 1\n', "sherpa"),
    ("правка первой из двух секций", "sufler:\n  diarize_backend: sherpa\nsufler:\n  diarize_backend: sherpa\n",
     'sufler:\n  diarize_backend: "nemotron"\nsufler:\n  diarize_backend: sherpa\n', "sherpa"),
    # Последняя секция без ключа. Приложение (построчный скан) видит sherpa — реальный вход кнопки:
    # отказ на сверке ожидаемого (YAML видит None).
    ("ключ только в первой секции", "sufler:\n  diarize_backend: sherpa\nsufler:\n  quiet: true\n",
     'sufler:\n  diarize_backend: "nemotron"\nsufler:\n  quiet: true\n', "sherpa"),
    # Тот же вход с expected=None (ожидаемое совпало с YAML) — отказ на сверке правки.
    ("ключ только в первой секции, ожидали None", "sufler:\n  diarize_backend: sherpa\nsufler:\n  quiet: true\n",
     'sufler:\n  diarize_backend: "nemotron"\nsufler:\n  quiet: true\n', None),
]


@pytest.mark.parametrize("name,old,new,expected", EDGE_REFUSED, ids=[c[0] for c in EDGE_REFUSED])
def test_edge_inputs_refused(name, old, new, expected):
    assert ie.verify_config_edit(_payload(old, new, expected)) is not None, name


def test_raw_bare_off_matches_yaml_false():
    # Голое off YAML читает как False, приложение видит строку «off».
    assert ie.verify_config_edit(_payload("sufler:\n  diarize_backend: off\n",
                                          'sufler:\n  diarize_backend: "nemotron"\n', "off")) is None


@pytest.mark.parametrize("payload", [
    [], {"old": "a: 1"}, _payload("a: 1", "a: 1", None, value="x"),
    {**_payload("a: 1", "a: 1", None), "key": "graph_dir"}, {**_payload("a: 1", "a: 1", None), "expected": 1},
])
def test_bad_payload_refused(payload):
    assert ie.verify_config_edit(payload) is not None


def _run(stdin: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    return subprocess.run([sys.executable, str(SCRIPT), "--verify-config-edit"], input=stdin, text=True,
                          capture_output=True, cwd=cwd, env=env, timeout=30)


def test_cli_contract_and_writes_nothing(tmp_path):
    tmp_path = tmp_path / "cwd"   # корень tmp_path занимает фикстура графа из conftest
    tmp_path.mkdir()
    ok = json.dumps(_payload("sufler:\n  diarize_backend: sherpa\n", 'sufler:\n  diarize_backend: "nemotron"\n',
                             "sherpa"), ensure_ascii=False)
    t0 = time.monotonic()
    r = _run(ok, tmp_path)
    elapsed = time.monotonic() - t0
    assert (r.returncode, r.stdout) == (0, "ok\n"), r.stderr
    assert elapsed < 5
    _, old, new, expected = CRITICAL[0]
    r = _run(json.dumps(_payload(old, new, expected)), tmp_path)
    assert r.returncode == 1
    assert r.stdout.startswith("отказ: ") and r.stdout.count("\n") == 1
    r = _run("не json", tmp_path)
    assert r.returncode == 1 and r.stdout.startswith("отказ: проверка упала")
    assert list(tmp_path.iterdir()) == [], "проверка ничего не пишет"


def test_cli_recursive_alias_refused(tmp_path):
    # Сверка n622p2-B-fix7 №4: рекурсивный алиас — отказ процесса, а не зависание или «ок».
    old = "a: &a [*a]\nsufler:\n  diarize_backend: sherpa\n"
    r = _run(json.dumps(_payload(old, old.replace("sherpa", '"nemotron"'), "sherpa")), tmp_path)
    assert r.returncode == 1 and r.stdout.startswith("отказ: проверка упала"), (r.stdout, r.stderr)


def test_preset_seed_of_second_pass():
    """Посев мастера (№822): ключа нет — дописан; ручное значение, которого не увидел
    построчный разбор (ключ в кавычках, `key :`), — отказ; иных значений посев не пишет."""
    def seed(old, new, expected=None, value="true"):
        return ie.verify_config_edit({"old": old, "new": new, "key": "graph_second_pass",
                                      "expected": expected, "value": value})
    assert seed("sufler:\n  graph: true\n", 'sufler:\n  graph: true\n  graph_second_pass: "true"\n') is None
    assert seed('sufler:\n  graph_second_pass: "true"\n', 'sufler:\n  graph_second_pass: "false"\n',
                expected="true", value="false") is None
    for owner in ('"graph_second_pass": false', "graph_second_pass : false"):
        old = f"sufler:\n  {owner}\n"
        assert seed(old, old + '  graph_second_pass: "true"\n') is not None, owner
    assert seed("sufler:\n  a: 1\n", 'sufler:\n  a: 1\n  graph_second_pass: "yes"\n', value="yes") is not None
