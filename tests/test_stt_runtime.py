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
    # Ветка разгрузки: план целиком из чистой функции, метка — константа, а
    # не литерал -1 (n=0 — валидный индекс голоса; ревью 21.08, DeepSeek).
    assert "stt_runtime.diarization_plan(" in loop[policy:split]
    # проводка аргументов без инверсии: `lagging=not lagging` прошёл бы
    # все строковые ассерты и чистые тесты (ревью 22.08, Codex)
    plan_call = loop[loop.index("stt_runtime.diarization_plan("):]
    plan_call = plan_call[:plan_call.index("))") + 2]
    assert "lagging=lagging," in plan_call
    assert "has_split=stt_runtime.has_split_tracker(spk_tracker))" in plan_call
    assert 'if plan == "shed":' in loop[policy:split]
    assert "jobs = [(chunk, stt_runtime.CHANNEL_LABEL_ONLY, None)]" in loop[policy:split]
    assert 'elif plan == "diarize":' in loop[policy:split]
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


# --- решения, вынесенные из замыканий daemon.main() (партия D, 22.08) -------
#
# Мутационный прогон 21.08: 53 из 53 мутантов daemon.py выжили — stt_loop,
# report_progress и главный цикл живут внутри main(), unit-тест их не видит.
# Каждая функция ниже держит инвариант, поломка которого стоила бы встречи.


def test_возраст_этапа_не_бывает_отрицательным():
    assert stt_runtime.stage_age(100.0, 90.0) == 10.0
    assert stt_runtime.stage_age(100.0, 105.0) == 0.0


def test_возраст_входа_для_телеметрии_хранит_none():
    """Приложение двигает lastAudioInputAt только по числу: «всегда null»
    выключал бы ветку «аудиопоток замер → перезапуск» навсегда."""
    assert stt_runtime.input_age_value(None) is None
    assert stt_runtime.input_age_value(1.2345) == 1.23
    assert stt_runtime.input_age_value(0) == 0.0


def test_принудительный_пульс_не_глушится_а_обычный_дросселируется():
    assert stt_runtime.progress_throttled(force=True, now=1.0, last=0.0, every=5.0) is False
    assert stt_runtime.progress_throttled(force=False, now=1.0, last=0.0, every=5.0) is True
    assert stt_runtime.progress_throttled(force=False, now=5.0, last=0.0, every=5.0) is False
    # last ≠ 0: с нулём `now - last` и `now + last` неотличимы — мутант
    # Sub→Add пережил первый прогон партии D
    assert stt_runtime.progress_throttled(force=False, now=10.0, last=8.0, every=5.0) is True
    assert stt_runtime.progress_throttled(force=False, now=13.0, last=8.0, every=5.0) is False


def test_лог_отставания_только_при_отставании_и_не_чаще_периода():
    assert stt_runtime.lag_log_due(lagging=False, now=100.0, last=0.0, every=30.0) is False
    assert stt_runtime.lag_log_due(lagging=True, now=29.0, last=0.0, every=30.0) is False
    assert stt_runtime.lag_log_due(lagging=True, now=30.0, last=0.0, every=30.0) is True
    assert stt_runtime.lag_log_due(lagging=True, now=100.0, last=90.0, every=30.0) is False
    assert stt_runtime.lag_log_due(lagging=True, now=120.0, last=90.0, every=30.0) is True


def test_переход_разгрузки_только_при_смене_состояния():
    assert stt_runtime.lag_transition(False, True) is True
    assert stt_runtime.lag_transition(True, False) is True
    assert stt_runtime.lag_transition(True, True) is False
    assert stt_runtime.lag_transition(False, False) is False


def test_догнал_только_при_живом_входе():
    """Мёртвый вход (None) и старые кадры — «жду кадров», а не «догнал»:
    ложный ✅ код уже чинил один раз (круг 3 по #362)."""
    assert stt_runtime.live_input_young_enough(None, 3.0) is False
    assert stt_runtime.live_input_young_enough(0.5, 3.0) is True
    assert stt_runtime.live_input_young_enough(3.0, 3.0) is False
    assert stt_runtime.live_input_young_enough(5.0, 3.0) is False


def test_план_диаризации_по_четырём_случаям():
    class WithSplit:
        def split(self, chunk, channel=None): ...

    assert stt_runtime.has_split_tracker(None) is False
    assert stt_runtime.has_split_tracker(object()) is False
    assert stt_runtime.has_split_tracker(WithSplit()) is True
    assert stt_runtime.diarization_plan(lagging=False, has_split=False) == "plain"
    assert stt_runtime.diarization_plan(lagging=True, has_split=False) == "plain"
    assert stt_runtime.diarization_plan(lagging=False, has_split=True) == "diarize"
    assert stt_runtime.diarization_plan(lagging=True, has_split=True) == "shed"
    assert stt_runtime.CHANNEL_LABEL_ONLY == -1, "0 — валидный индекс голоса"


def test_пульс_и_stalled_по_порогам():
    assert stt_runtime.heartbeat_due(now=30.0, last=0.0) is False
    assert stt_runtime.heartbeat_due(now=30.1, last=0.0) is True
    assert stt_runtime.heartbeat_due(now=100.0, last=80.0) is False
    assert stt_runtime.heartbeat_due(now=111.0, last=80.0) is True
    assert stt_runtime.stall_log_due(stage_age_s=29.9, now=100.0, last=0.0) is False
    assert stt_runtime.stall_log_due(stage_age_s=30.0, now=100.0, last=0.0) is True
    assert stt_runtime.stall_log_due(stage_age_s=30.0, now=100.0, last=80.0) is False
    # ровно период — пишем (>=), а не ждём ещё такт
    assert stt_runtime.stall_log_due(stage_age_s=30.0, now=130.0, last=100.0) is True


def test_отказ_записи_на_диск_красится_по_подстроке():
    assert stt_runtime.is_recording_failure(
        "⚠️ подсказки отстают… ЗАПИСЬ НА ДИСК НЕ ИДЁТ — этот звук не вернуть") is True
    assert stt_runtime.is_recording_failure("канал не открылся") is False
