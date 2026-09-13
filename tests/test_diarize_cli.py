"""Разбор аргументов офлайн-диаризатора: до 13.09 значение `--channel right`
уезжало в штамп файла, `--channel=right` молча давало left, `--speakers 2`
игнорировался (аудит 13.09, DS I1/I2/M5, GLM M3)."""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import diarize  # noqa: E402


def test_channel_value_does_not_leak_into_positionals():
    pos, channel, n = diarize.parse_args(["rec.wav", "--channel", "right", "1400"])
    assert (pos, channel, n) == (["rec.wav", "1400"], "right", -1)
    pos, channel, n = diarize.parse_args(["--channel", "right", "rec.wav"])
    assert (pos, channel, n) == (["rec.wav"], "right", -1)


def test_both_key_forms_are_equal():
    assert diarize.parse_args(["rec.wav", "--channel=right"])[1] == "right"
    assert diarize.parse_args(["rec.wav", "--speakers=2"])[2] == 2
    assert diarize.parse_args(["rec.wav", "--speakers", "3"])[2] == 3
    assert diarize.parse_args(["rec.wav"])[1:] == ("left", -1)


@pytest.mark.parametrize("argv", [
    ["rec.wav", "--channel", "centre"],
    ["rec.wav", "--channel"],
    ["rec.wav", "--speakers=two"],
    ["rec.wav", "--loud"],
    ["--channel", "right"],
])
def test_bad_values_fail_loudly_instead_of_silently(argv):
    with pytest.raises(SystemExit):
        diarize.parse_args(argv)
