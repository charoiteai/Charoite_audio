"""Load shedding keeps text realtime while preserving full audio on disk."""
from __future__ import annotations

import ast
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
    for metric in ("state", "stage", "stage_age_seconds", "backlog_seconds",
                   "diarization_ms", "transcription_ms", "input_age_seconds",
                   "recording_ok", "channels"):
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
    assert stt_runtime.stage_is_stalled(stage_age_s=29.9) is False
    assert stt_runtime.stage_is_stalled(stage_age_s=30.0) is True
    assert stt_runtime.stall_log_due(stage_age_s=29.9, now=100.0, last=0.0) is False
    assert stt_runtime.stall_log_due(stage_age_s=30.0, now=100.0, last=0.0) is True
    assert stt_runtime.stall_log_due(stage_age_s=30.0, now=100.0, last=80.0) is False
    # ровно период — пишем (>=), а не ждём ещё такт
    assert stt_runtime.stall_log_due(stage_age_s=30.0, now=130.0, last=100.0) is True


def test_main_heartbeat_exposes_stall_without_forging_stt_progress():
    """Главный поток видит native-hang, но событие остаётся `hb`.

    Назвать его `stt_progress` означало бы двигать Swift-якорь и навсегда
    спрятать зависший потребитель за живым main thread.
    """
    source = (REPO / "src" / "daemon.py").read_text(encoding="utf-8")
    main_loop = source[source.index("        while not stop.is_set():", source.index("last_hb =")):
                       source.index("    except KeyboardInterrupt:")]
    heartbeat = main_loop[main_loop.index('hb_event = {"type": "hb"'):
                          main_loop.index("# Сторож слоя авто-подсказок")]
    assert '"stt_stalled": stt_runtime.stage_is_stalled(' in heartbeat
    assert "threshold=STT_STALL_THRESHOLD" in heartbeat
    assert '"type": "stt_progress"' not in heartbeat
    # О диске судит тоже сервер: снапшот recording_ok в hb, потому что
    # stt_progress замерзает вместе с STT (круг-1 GLM по #431, I1);
    # вызов обёрнут — телеметрия не роняет main-loop (круг-2 DS, M3).
    assert 'hub.health_snapshot()["recording_ok"]' in heartbeat
    assert heartbeat.index("try:") < heartbeat.index("hub.health_snapshot")


def test_отказ_записи_на_диск_красится_по_подстроке():
    assert stt_runtime.is_recording_failure(
        "⚠️ подсказки отстают… ЗАПИСЬ НА ДИСК НЕ ИДЁТ — этот звук не вернуть") is True
    assert stt_runtime.is_recording_failure("канал не открылся") is False


def test_realtime_factor_turns_milliseconds_into_a_verdict():
    """«Транскрипция 3200 мс» без длины звука не значит ничего.

    Паспорт gigaam-v3 на этой машине — 28× (17,6 с звука за 0,63 с, замер
    16.07). Отставание при RTF около единицы означает, что модель работает не
    в том режиме, и лечится профилированием, а не придерживанием соседей по
    нагрузке (№105).
    """
    assert stt_runtime.realtime_factor(17.6, 630) == 27.94
    assert stt_runtime.realtime_factor(4.0, 3225) == 1.24
    # считать не из чего — молчим, а не выдумываем ноль
    assert stt_runtime.realtime_factor(0, 3225) is None
    assert stt_runtime.realtime_factor(4.0, 0) is None
    assert stt_runtime.realtime_factor(-1, 100) is None


def test_lag_line_carries_time_audio_and_rtf():
    """Без отметки времени эпизоды не разложить по нагрузке машины,
    без audio_s и rtf — не отличить медленную модель от большого куска."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "src"
           / "daemon.py").read_text(encoding="utf-8")
    line = src[src.index('print(f"{dt.datetime.now():%H:%M:%S} stt-health'):]
    line = line[:line.index("file=sys.stderr")]
    for field in ("backlog_s=", "cycle_ms=", "diarization_ms=",
                  "transcription_ms=", "audio_s=", "rtf="):
        assert field in line, f"в строке отставания нет {field}"
    event = src[src.index('"type": "stt_progress"'):]
    event = event[:event.index("})")]
    assert '"audio_s"' in event and '"rtf"' in event, "событие без RTF"


def test_hint_pulse_names_the_reason_and_separates_waiting_from_work():
    """«fails=22» не отвечает на вопрос «почему», а одна цифра времени лжёт.

    27.08 подсказки отказали 22 раза подряд, а причину — упавшую Ollama —
    пришлось искать в логах руками (№91). Круг по PR #440 добавил к этому две
    поправки: ветка «замок занят» выходила до записи и оставляла в пульсе
    чужую причину от прошлой попытки (все три головы), а единая цифра времени
    смешивала ожидание чужой генерации с работой модели — замок держат и нить,
    и минутки, и ответ на вопрос (GLM).
    """
    src = (pathlib.Path(__file__).resolve().parent.parent / "src"
           / "daemon.py").read_text(encoding="utf-8")
    pulse = src[src.index('auto = hint_state.get("auto")'):]
    pulse = pulse[:pulse.index("file=sys.stderr")]
    for field in ("wait_ms=", "model_ms=", "reason="):
        assert field in pulse, f"пульс молчит о {field}"
    assert 'hint_state.get("auto")' in pulse or "hint_state.get('auto')" in pulse, \
        "пульс читает общее состояние — ручной запрос затрёт причину авто-цикла"

    # Выходы считаем по дереву, а не по подстрокам: `gen.count("_telemetry(")`
    # засчитывал и строку `def _telemetry(`, то есть гейт пропустил бы удаление
    # вызова из ветки (GLM, круг-2).
    tree = ast.parse(src)
    gen = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "gen_hint")

    def _is_telemetry(stmt) -> bool:
        return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "_telemetry")

    unmarked: list[int] = []

    def _walk(body, seen: bool) -> None:
        """Пройти тело по порядку: к каждому выходу — со своей историей пути."""
        for stmt in body:
            if _is_telemetry(stmt):
                seen = True
                continue
            if isinstance(stmt, ast.Return):
                if not seen:
                    unmarked.append(stmt.lineno)
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue                       # сам _telemetry и прочие помощники
            # ветвления: каждая ветка идёт со своей копией истории
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if inner:
                    _walk(inner, seen)
            for handler in getattr(stmt, "handlers", []):
                _walk(handler.body, seen)

    _walk(gen.body, False)
    assert not unmarked, f"выход без записи исхода, строки: {unmarked}"

    assert 'hint_state["manual" if manual else "auto"]' in src, \
        "ручная и авто подсказки снова пишут в одно поле"


def test_lag_line_carries_totals_because_the_lagging_cycle_never_splits():
    """Счётчик копится за всю запись, а не за цикл — иначе он мерит пустоту.

    Круг-1, GLM: строка `state=lagging` пишется ТОЛЬКО при отставании, а при
    отставании раскладка уходит в `shed` и отдаёт один кусок на чанк целиком.
    Значит в сохраняемой строке `calls` всегда равнялось бы числу каналов, а
    `shortest_s` — длине чанка, независимо от того, дробит ли конвейер звук в
    здоровых циклах. Гипотеза, ради которой всё затевалось, проверялась бы
    данными, где явление structurally отсутствует.
    """
    src = (pathlib.Path(__file__).resolve().parent.parent / "src"
           / "daemon.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    lag = src[src.index('stt-health state=lagging'):]
    lag = lag[:lag.index("file=sys.stderr")]
    for field in ("calls=", "shortest_s=", "rtf_total="):
        assert field in lag, f"строка отставания молчит о {field}"
    assert '"calls_total"' in src and '"rtf_total"' in src, (
        "итоги есть в логе, но не в stt_progress — приложение их не увидит"
    )

    # Ключевое: накопители НЕ обнуляются внутри цикла разбора батча.
    loop = next(n for n in ast.walk(tree)
                if isinstance(n, ast.While) and "cycle_audio_s" in ast.dump(n))
    # Ищем именно ОБНУЛЕНИЕ (присваивание константы), а не обновление
    # минимума `shortest_piece_s = piece_s` — оно как раз законно.
    reset_in_loop = [tgt.id for n in ast.walk(loop)
                     if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                     for tgt in n.targets
                     if isinstance(tgt, ast.Name)
                     and tgt.id in ("total_stt_calls", "total_audio_s",
                                    "shortest_piece_s", "total_transcription_ms")]
    assert not reset_in_loop, (
        "итог обнуляется в цикле — снова получим цифры одного отстающего кадра"
    )


def test_the_call_counter_never_breaks_recognition():
    """Телеметрия падает — распознавание продолжается.

    Тот же контракт, что у подсчёта звука: деление на ноль или странный
    кусок не должны ронять поток STT.
    """
    src = (pathlib.Path(__file__).resolve().parent.parent / "src"
           / "daemon.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
        if "total_stt_calls" in body and "cycle_audio_s" in body:
            names = {n.id for h in node.handlers for n in ast.walk(h.type or ast.Name(id=""))
                     if isinstance(n, ast.Name)}
            assert {"TypeError", "ValueError", "ZeroDivisionError"} <= names, (
                f"счётчик вызовов прикрыт не теми исключениями: {names}"
            )
            guarded = True
    assert guarded, "счётчик вызовов не обёрнут вовсе"
