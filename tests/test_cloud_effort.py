"""Усилие облачного разбора (cloud_effort) — сторож обеих веток команды.

Headless `claude -p` без явного усилия идёт на «auto (currently high)», а из
shell с глобальным env машины — на max; `--effort` env не перекрывает,
`--settings {"env": …}` — перекрывает (замер 08.09, №189). Выпадет
`*effort_flags` из одной ветки cloud_enrich_command — разбор молча вернётся
к high и к таймаутам; этот файл — чтобы такое не прошло зелёным.
"""
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cloud  # noqa: E402
import graph_updater  # noqa: E402

EXPECTED = ["--settings", json.dumps({"env": {"CLAUDE_CODE_EFFORT_LEVEL": "medium"}})]


def _has(cmd: list[str], pair: list[str]) -> bool:
    return any(cmd[i:i + 2] == pair for i in range(len(cmd) - 1))


def test_default_is_medium_and_config_word_wins():
    assert cloud.effort({}) == "medium"
    assert cloud.effort({"sufler": {"cloud_effort": " HIGH "}}) == "high"
    assert cloud.effort({"sufler": {"cloud_effort": "auto"}}) == "auto"


def test_unknown_word_falls_back_to_default_out_loud(capsys):
    assert cloud.effort({"sufler": {"cloud_effort": "чушь"}}) == "medium"
    err = capsys.readouterr().err
    assert "cloud_effort" in err and "чушь" in err and "medium" in err


def test_unknown_key_is_a_caller_error():
    with pytest.raises(KeyError):
        cloud.effort({}, "cloud_live_effort")


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
