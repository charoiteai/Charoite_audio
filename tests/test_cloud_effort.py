"""Усилие (cloud_effort) и модель (cloud_debrief_model) облачного разбора.

Headless `claude -p` без явного усилия идёт на «auto (currently high)», а из
shell с глобальным env машины — на max; `--effort` env не перекрывает,
`--settings {"env": …}` — перекрывает (замер 08.09, №189). Выпадет
`*effort_flags` из одной ветки cloud_enrich_command — разбор молча вернётся
к high и к таймаутам; этот файл — чтобы такое не прошло зелёным.

Тот же сторож и у модели: разбор встречи читает свой ключ
`cloud_debrief_model`, а не соседние ночные/живые; ключ и его значение видны
в логе разбора. Подмена ключа в точке выхода без этих тестов прошла бы
зелёной — `cloud_enrich_command` зовётся, а литерала вызова `claude -p` в
`cloud_review.py` нет, и сканер усилия его не видел.
"""
import json
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import cloud  # noqa: E402
import cloud_review  # noqa: E402
import graph_updater  # noqa: E402

EXPECTED = ["--settings", json.dumps({"env": {"CLAUDE_CODE_EFFORT_LEVEL": "medium"}})]


def _has(cmd: list[str], pair: list[str]) -> bool:
    return any(cmd[i:i + 2] == pair for i in range(len(cmd) - 1))


def test_default_is_medium_and_config_word_wins():
    assert cloud.effort({}) == "medium"
    assert cloud.effort({}, "cloud_live_effort") == "low"
    assert cloud.effort({"sufler": {"cloud_effort": " HIGH "}}) == "high"
    assert cloud.effort({"sufler": {"cloud_effort": "auto"}}) == "auto"


def test_unknown_word_falls_back_to_default_out_loud(capsys):
    assert cloud.effort({"sufler": {"cloud_effort": "чушь"}}) == "medium"
    err = capsys.readouterr().err
    assert "cloud_effort" in err and "чушь" in err and "medium" in err


def test_unknown_key_is_a_caller_error():
    with pytest.raises(KeyError):
        cloud.effort({}, "cloud_nope_effort")


def test_both_command_branches_carry_the_effort_settings():
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    with_graph = graph_updater.cloud_enrich_command(
        cfg, claude_bin="claude", prompt="p", model="m", env={})
    text_only = graph_updater.cloud_enrich_command(
        cfg, claude_bin="claude", prompt="p", model="m", env={}, graph_available=False)
    assert _has(with_graph, EXPECTED), with_graph
    assert _has(text_only, EXPECTED), text_only
    # --settings стоит до "--setting-sources" "" и не съедает соседей
    i = with_graph.index("--settings")
    assert with_graph[i + 2].startswith("--"), with_graph[i:i + 3]


def test_precomputed_effort_is_used_as_is():
    cfg = {"sufler": {"cloud_effort": "low"}}
    cmd = graph_updater.cloud_enrich_command(
        cfg, claude_bin="claude", prompt="p", model="m", env={}, effort="xhigh")
    assert _has(cmd, ["--settings", json.dumps({"env": {"CLAUDE_CODE_EFFORT_LEVEL": "xhigh"}})])


def test_effort_warning_is_a_plain_string_for_the_log_head():
    assert cloud.effort_warning({}) == ""
    assert cloud.effort_warning({"sufler": {"cloud_effort": "high"}}) == ""
    w = cloud.effort_warning({"sufler": {"cloud_effort": "xxhigh"}})
    assert "xxhigh" in w and "medium" in w
    with pytest.raises(KeyError):
        cloud.effort_warning({}, "нет-такого")


# Пара `claude…, "-p"` где угодно в литерале (не только сразу после «[»):
# `[*flags, claude, "-p"…]` и `[cloud.claude_bin(), "-p"…]` иначе выпадали из
# сторожа молча (DS M1, GLM M4 по #528).
_CALL = re.compile(r"(?:cloud\.)?claude\w*(?:\(\))?\s*,\s*\"-p\"")
# Какой ключ усилия обязан стоять у вызова в этом файле (GLM M2 по #528)
_KEY_BY_FILE = {
    "src/daemon.py": 'cloud.effort(cfg, "cloud_live_effort")',
    "scripts/nightly_dossier_review.py": 'cloud.effort(cfg, "cloud_night_effort")',
    "scripts/nightly_claude_cores.py": 'cloud.effort(cfg, "cloud_night_effort")',
    "src/graph_updater.py": "effort_flags",
}


def test_every_headless_call_carries_the_right_effort():
    """Каждый `claude -p` в src/ и scripts/ несёт усилие и именно свой ключ:
    нить/ответ — cloud_live_effort, ночь — cloud_night_effort, разбор —
    effort_flags из cloud_effort. Иначе вызов идёт на «auto»/max из окружения
    машины (№214), а копипаста чужого ключа проходила бы молча."""
    seen = 0
    for folder in ("src", "scripts"):
        for path in sorted((REPO / folder).glob("*.py")):
            text = path.read_text(encoding="utf-8")
            rel = f"{folder}/{path.name}"
            for m in _CALL.finditer(text):
                seen += 1
                window = text[m.start():m.start() + 4000]   # промпты в списке длинные
                expected = _KEY_BY_FILE.get(rel)
                assert expected is not None, f"{rel}: новый вызов claude -p — добавь его ключ в _KEY_BY_FILE"
                assert expected in window, f"{rel}: вызов claude -p без усилия или с чужим ключом"
    assert seen == 6, f"регэксп нашёл {seen} вызовов, ждали 6 (daemon×2, graph_updater×2, ночь×2) — протух"


def test_night_effort_default_is_high():
    assert cloud.effort({}, "cloud_night_effort") == "high"


# Прямой вызов выбора модели: `cloud.model(cfg, "<ключ>")` в src/ и scripts/.
# `cloud_review.py` строит команду не сам, а через cloud_enrich_command, и в
# сканере усилия по литералу `claude -p` не виден — поэтому точка выхода у
# него держится здесь, отдельным перечнем (факт 2 задания).
_MODEL_CALL = re.compile(r"cloud\.model\(\s*cfg\s*,\s*[\"']([a-z_]+)[\"']")
# Точка выхода → ключ(и) модели, которые она вправе читать. Разбор встречи —
# свой ключ (Sonnet по умолчанию), ночные скрипты — cloud_model, живой слой —
# cloud_live_model/cloud_hints_model.
_MODEL_KEYS_BY_FILE = {
    "scripts/cloud_review.py": {"cloud_debrief_model"},
    "scripts/nightly_claude_cores.py": {"cloud_model"},
    "scripts/nightly_dossier_review.py": {"cloud_model"},
    "src/daemon.py": {"cloud_live_model", "cloud_hints_model"},
}


def test_every_model_exit_reads_its_documented_key():
    """Точка выхода читает СВОЙ ключ модели, а не соседний.

    Подмена `cloud_debrief_model` на `cloud_model`/`cloud_live_model` в
    `cloud_review.py` раньше не краснила ни один тест (факт 9 задания) — и
    разбор встречи молча уходил на ночную или живую модель. Сканер знает и
    `cloud_review.py`, и оба ночных скрипта, и запрещает любой ключ вне
    перечня — новый прямой `cloud.model(` обязан появиться здесь.
    """
    checked: set[str] = set()
    for folder in ("src", "scripts"):
        for path in sorted((REPO / folder).glob("*.py")):
            if path.name == "cloud.py":
                continue
            rel = f"{folder}/{path.name}"
            keys = set(_MODEL_CALL.findall(path.read_text(encoding="utf-8")))
            if not keys:
                continue
            checked.add(rel)
            assert keys == _MODEL_KEYS_BY_FILE.get(rel), (
                f"{rel}: читает {sorted(keys)}, а перечень разрешает "
                f"{sorted(_MODEL_KEYS_BY_FILE.get(rel, ()))} — ключ модели разошёлся")
    assert checked == set(_MODEL_KEYS_BY_FILE), (
        f"сканируемые точки выхода разошлись с перечнем: нашли {sorted(checked)}, "
        f"ждали {sorted(_MODEL_KEYS_BY_FILE)}")


_SENTINEL_REPORT = ("- **Решение:** оставить граф закрытым\n"
                    "- **Поручение:** проверить настройку\n"
                    "- **Риск:** файловый доступ не выдавался\n")


def test_debrief_log_names_the_debrief_model_and_no_neighbour(tmp_path, monkeypatch):
    """Модель разбора — один объект: она уходит в команду и целиком в лог
    (шапку и строку «за N мин»), а значений соседних ключей там нет.

    Значения — отличимые метки, ни одна не равна дефолту: на дефолтах тест
    был бы зелёным и при лжи лога (соседний ключ совпал бы с разбором).
    """
    debrief, night, live = ("claude-debrief-sentinel", "claude-night-sentinel",
                            "claude-live-sentinel")
    for value in (debrief, night, live):
        assert value not in cloud.DEFAULTS.values(), "метка совпала с дефолтом — тест слепой"
    stamp = "2026-07-15_1400"
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст встречи\n", encoding="utf-8")
    graph = tmp_path / "граф"
    graph.mkdir()
    rev = transcripts / f"{stamp}_ревизия.md"
    log = tmp_path / "cloud.log"
    captured: dict = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        kwargs["stdout"].write(_SENTINEL_REPORT)
        return Result()

    # Графа нет — прогон только текстом: без замка, бэкапов и доставки,
    # тесту нужна одна команда и её лог.
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: False)
    monkeypatch.setattr(cloud_review.cloud, "claude_bin", lambda: "/x/claude")
    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_debrief_model": debrief,
                      "cloud_model": night, "cloud_live_model": live}}
    assert cloud_review._run_once(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == debrief, cmd
    text = log.read_text(encoding="utf-8")
    head = text.splitlines()[0]
    saved = [ln for ln in text.splitlines() if "ревизия сохранена" in ln]
    assert debrief in head and len(saved) == 1 and debrief in saved[0], text
    assert night not in text and live not in text, "в лог просочился соседний ключ модели"
