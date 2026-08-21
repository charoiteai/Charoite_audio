"""Load shedding keeps text realtime while preserving full audio on disk."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import stt_runtime  # noqa: E402


def decide(backlog: float, active: bool = False, chunk: float = 3.0) -> bool:
    return stt_runtime.should_shed_diarization(
        backlog_seconds=backlog,
        active=active,
        chunk_seconds=chunk,
    )


def test_normal_overlap_does_not_disable_diarization():
    assert decide(0.5) is False
    assert decide(5.9) is False


def test_two_chunks_of_backlog_choose_one_stt_job_over_live_diarization():
    assert decide(6.0) is True


def test_hysteresis_prevents_mode_flapping():
    assert decide(4.0, active=True) is True
    assert decide(1.6, active=True) is True
    assert decide(1.5, active=True) is False


def test_threshold_scales_with_non_default_chunk_size():
    assert decide(7.9, chunk=4.0) is False
    assert decide(8.0, chunk=4.0) is True


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_broken_telemetry_fails_safe_to_cheaper_path(bad):
    assert decide(bad) is True


def test_daemon_measures_and_sheds_before_positional_split():
    """Policy tests alone are theatre if the hot loop never applies it."""
    source = (REPO / "src" / "daemon.py").read_text(encoding="utf-8")
    loop = source[source.index("    def stt_loop():"):
                  source.index("    # Промпт и фильтр тезисов")]
    policy = loop.index("stt_runtime.should_shed_diarization")
    split = loop.index("res = spk_tracker.split")
    assert policy < split
    assert "jobs = [(chunk, -1, None)]" in loop[policy:split]
    assert '"type": "stt_progress"' in loop
    for metric in ("backlog_seconds", "diarization_ms", "transcription_ms",
                   "input_age_seconds", "recording_ok"):
        assert f'"{metric}"' in loop
    assert "mark_stt_stage(\"diarization\")" in loop
    assert "mark_stt_stage(\"transcription\")" in loop
    assert "stt-health state=stalled" in source


def test_шестисекундный_этаж_входа_держится_при_мелком_чанке():
    """Докстринг обещает «не раньше шести секунд» — при chunk=2 порог обязан
    остаться 6.0, а не 2*chunk=4 (ревью 21.08, GLM: мутация 6.0→0 выживала,
    все прежние тесты звали функцию с chunk>=3, где этаж не работал)."""
    assert stt_runtime.should_shed_diarization(
        backlog_seconds=5.9, active=False, chunk_seconds=2.0) is False
    assert stt_runtime.should_shed_diarization(
        backlog_seconds=6.0, active=False, chunk_seconds=2.0) is True


def test_секундный_этаж_восстановления_держится_при_мелком_чанке():
    """recover = max(1.0, chunk/2): при chunk=1.5 порог — 1.0, не 0.75."""
    assert stt_runtime.should_shed_diarization(
        backlog_seconds=0.8, active=True, chunk_seconds=1.5) is False
    assert stt_runtime.should_shed_diarization(
        backlog_seconds=1.1, active=True, chunk_seconds=1.5) is True


def test_выбор_ветки_разгрузки_закреплён_поведением():
    """and→or в инлайновом условии выключал живую диаризацию навсегда при
    зелёных строковых ассертах (ревью 21.08, GLM) — теперь выбор ветки живёт
    чистой функцией и держится этими четырьмя случаями."""
    assert stt_runtime.use_positional_split(lagging=False, has_split=True) is True
    assert stt_runtime.use_positional_split(lagging=True, has_split=True) is False
    assert stt_runtime.use_positional_split(lagging=False, has_split=False) is False
    assert stt_runtime.use_positional_split(lagging=True, has_split=False) is False
