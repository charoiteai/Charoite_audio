"""Характеризация нормализаторов диаризации: ключ движка и режим живого потока.

Таблица — `tests/fixtures/diarize_normalizers.yaml`; её читают эти тесты и часть 2
(Swift) при проверке записи ключа в приложении. Каждое `text` идёт через
`yaml.safe_load`: голое `off` YAML 1.1 читает как False, `~` — как None, и
нормализатор обязан свести это к тому же ответу, что и явные строки.

Первый коммит характеризации пиннит НЫНЕШНИЕ выражения (они ещё живут по месту —
в `rebuild_transcript.call_channel_engine` и `live_nemotron.start`). Следующий
коммит выносит их в `diarize_nemotron`; ожидания (фикстура) при этом не меняются,
меняются только вызываемые функции.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import diarize_nemotron as nem  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "diarize_normalizers.yaml"


def _rows(table: str) -> list[dict]:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    return data[table]


def _engine_key(raw) -> str:
    return nem.normalize_engine_key(raw)


def _live_mode(raw) -> str:
    return nem.normalize_live_mode(raw)


@pytest.mark.parametrize("row", _rows("engine"), ids=[r["text"] for r in _rows("engine")])
def test_the_engine_key_normalizes_as_the_table_says(row):
    key = _engine_key(yaml.safe_load(row["text"]))
    assert key == row["key"]
    assert (key in ("sherpa", "nemotron")) is row["known"], "неизвестное не сворачивается в sherpa"


@pytest.mark.parametrize("row", _rows("live"), ids=[r["text"] for r in _rows("live")])
def test_the_live_mode_normalizes_as_the_table_says(row):
    mode = _live_mode(yaml.safe_load(row["text"]))
    assert mode == row["mode"]
    assert (mode in ("off", "shadow", "on")) is row["known"], "неизвестное не сворачивается в off"


def test_the_table_covers_every_name_from_the_task():
    """Страховка от того, что таблицу урежут: значения из постановки обязаны
    остаться — именно их сверит с записью ключа часть 2 (Swift)."""
    engine = {r["text"] for r in _rows("engine")}
    live = {r["text"] for r in _rows("live")}
    obshchee = {"", "~", "null", "Null", "NULL", "0", '""', '"sherpa"', '" Sherpa "', "nemotron", "foo"}
    assert obshchee <= engine, sorted(obshchee - engine)
    for slovo in ("false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"):
        assert slovo in engine and slovo in live, slovo
    assert {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON", "shadow"} <= live


# --- сторожа: вызовы берут нормализатор, а не свою копию ---------------------

def test_live_start_reads_the_shared_mode_normalizer(monkeypatch, tmp_path):
    """`live_nemotron.start` спрашивает режим у общего нормализатора: подмена его
    результата видна в исходе старта. Своя копия выражения подмену не заметила бы."""
    import live_nemotron as ln

    monkeypatch.setattr(nem, "normalize_live_mode", lambda raw: "off")
    says = []
    out = ln.start({"sufler": {"live_nemotron": "maybe"}}, root=tmp_path, stamp="s", sr=16000,
                   labels={"blackhole", "mic"}, say=says.append,
                   memory=lambda: {"pressure": 1, "swap_used_mb": 0})
    assert out is ln.NO_SHADOW and says == [], "неизвестный режим прочитан мимо общего нормализатора"


def test_call_channel_engine_reads_the_shared_key_normalizer(monkeypatch):
    """`rebuild_transcript.call_channel_engine` спрашивает ключ у общего нормализатора:
    подмена результата называет неизвестный движок даже там, где конфиг говорил sherpa."""
    import rebuild_transcript as rt
    monkeypatch.setattr(nem, "normalize_engine_key", lambda raw: "foo")
    segs, reason = rt.call_channel_engine({"sufler": {"diarize_backend": "sherpa"}},
                                          pathlib.Path("нет.wav"), 1.0, gate_call=False)
    assert segs is None and "'foo' неизвестен" in reason


# --- engine_state: одно определение «что стоит и что выбрано» -----------------

def test_engine_state_reports_layout_and_keys(tmp_path, monkeypatch):
    """Состояние — без сети, MLX и запуска движка: ключи, настройка, интерпретатор,
    окружение, веса и режим живого потока одним словарём."""
    installed = nem.engine_python(tmp_path)
    installed.parent.mkdir(parents=True)
    installed.write_text("#", encoding="utf-8")
    d = tmp_path / "models" / "diar" / "nemotron"
    d.mkdir(parents=True)
    (d / "config.json").write_text('{"model_type": "nemotron_diarization", "num_speakers": 8}',
                                   encoding="utf-8")
    (d / "model.safetensors").write_bytes(b"x" * nem.MIN_WEIGHTS_BYTES)
    state = nem.engine_state(tmp_path, {"sufler": {
        "diarize_backend": " Nemotron ", "nemotron_python": "", "live_nemotron": "on"}})
    assert state["backend"] == "nemotron" and state["live_mode"] == "on"
    assert state["nemotron_python"] == "" and state["nemotron_python_set"] is False
    assert state["interpreter"] == str(installed) and state["interpreter_reason"] == ""
    assert state["environment"] is True and state["weights"] is True
    assert state["weights_reason"] is None


def test_engine_state_without_a_config_is_all_empty(tmp_path):
    """Пустой cfg (пример конфига) — не падение: ключи по умолчанию, окружения и весов нет."""
    state = nem.engine_state(tmp_path, {})
    assert state["backend"] == "sherpa" and state["live_mode"] == "off"
    assert state["environment"] is False and state["weights"] is False
    assert state["interpreter_reason"], "нечего работать — причина названа"
    assert "нет каталога" in state["weights_reason"]
