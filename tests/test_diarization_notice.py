"""Нехватка модели диаризации обязана быть названа вслух.

`sufler.live_diarize` включён по умолчанию, а ERes2Net-эмбеддер в поставку не
входит: `models/diar/embedding.onnx` появляется только если пользователь его
скачал (docs/DIARIZATION.md). Значит «модели нет» — не редкая авария, а
состояние по умолчанию сразу после установки.

Раньше демон в этом случае молчал: ветка `if emb_model.exists()` просто не
выполнялась. Исключение при загрузке модели он сообщал, штатное отсутствие —
нет. Человек читает в README про «Собеседник 1/2/…» по голосам, ведёт встречу
на три голоса, получает всех под одной канальной меткой и не знает, почему;
диагноз лежит в другом месте (`scripts/doctor.py`), до которого надо
догадаться.

Продукт, который тихо отдаёт результат хуже обещанного, ломает доверие
сильнее, чем продукт, который честно говорит «этой части нет».
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from diarize_live import availability_note  # noqa: E402

MISSING = pathlib.Path("/nonexistent/models/diar/embedding.onnx")


def test_silence_is_not_an_option_when_the_model_is_missing():
    note = availability_note(True, MISSING)
    assert note, "модели нет, а демон ничего не сказал"
    assert "embedding.onnx" in note, "не сказано, какого файла не хватает"
    assert "DIARIZATION" in note, "не сказано, где взять модель"


def test_turned_off_in_config_is_reported_with_the_key_name():
    note = availability_note(False, MISSING)
    assert note and "live_diarize" in note, \
        "выключено пользователем — надо назвать ключ, чтобы он нашёл его в конфиге"


def test_nothing_to_say_when_diarization_works(tmp_path):
    model = tmp_path / "embedding.onnx"
    model.write_bytes(b"not a real model, only existence matters here")
    assert availability_note(True, model) is None


def test_note_explains_what_happens_instead():
    """Мало сказать «нет модели» — надо сказать, что человек получит вместо."""
    for note in (availability_note(True, MISSING), availability_note(False, MISSING)):
        assert "канал" in note, f"не сказано, что метки пойдут по каналам: {note!r}"
