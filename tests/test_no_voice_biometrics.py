"""«Голосовая биометрия не хранится» — обещание, у которого не было сторожа.

PRIVACY на трёх языках утверждает: живая диаризация держит эмбеддинги голосов
в RAM только на время встречи, ничего производного от голоса на диск не
пишется. ROADMAP называет это принципом дизайна: узнавание спикеров остаётся
социальным (представился, к нему обратились), а не построенным на хранимых
отпечатках.

Сегодня это правда — но держится на внимательности. Достаточно добавить кэш
эмбеддингов «чтобы не пересчитывать при пересборке стенограммы», и обещание
сломается молча: слепок голоса это самые чувствительные данные из всех, что
тут могли бы лежать, и утечка такого файла необратима — голос не сменить, как
пароль.

Файл держит два разных требования:

    1. Структурно: модули, работающие со звуком, не умеют сериализовать
       векторы. Никакого np.save, pickle, tofile и подобного.
    2. Поведенчески: прогон живого трекера по звуку не создаёт на диске
       ничего. Это сильнее любого чтения кода — если файл всё-таки появится,
       тест увидит его, как бы он ни назывался.

Второй тест требует установленной модели (`scripts/get_models.py --diar`) и
пропускается там, где её нет: в CI и у контрибьютора модели не будет, и
притворяться зелёным по существу нечестно.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

# Модули, через которые проходит звук и всё, что из него считается.
VOICE_MODULES = ("diarize_live.py", "diarize.py", "audio.py",
                 "rebuild_transcript.py", "transcribe_file.py", "daemon.py")

# Формы, которыми вектор превращается в файл. Каждая — готовый способ
# случайно завести биометрическую базу.
SERIALIZERS = ("np.save", "numpy.save", "np.savez", "numpy.savez",
               "pickle.dump", "pickle.dumps", "joblib.dump",
               ".tofile(", "torch.save", "np.savetxt", "numpy.savetxt")

MODEL = REPO / "models" / "diar" / "embedding.onnx"


def test_voice_modules_cannot_serialise_vectors():
    offenders = []
    for name in VOICE_MODULES:
        text = (SRC / name).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            for form in SERIALIZERS:
                if form in code:
                    offenders.append(f"{name}:{i}: {form}")
    assert not offenders, (
        "в модулях звука появилась сериализация — проверьте, не пишется ли "
        "слепок голоса на диск:\n  " + "\n  ".join(offenders))


def test_the_promise_is_written_where_users_read_it():
    """Обещание должно стоять во всех трёх PRIVACY, а не только в английском."""
    for doc in ("PRIVACY.md", "PRIVACY.ru.md", "PRIVACY.zh.md"):
        text = (REPO / doc).read_text(encoding="utf-8")
        assert "biometric" in text.lower() or "биометри" in text or "声纹" in text, \
            f"{doc} не обещает пользователю, что слепки голоса не хранятся"


@pytest.mark.skipif(not MODEL.exists(),
                    reason="нет models/diar/embedding.onnx — scripts/get_models.py --diar")
def test_live_tracking_writes_nothing_to_disk(tmp_path, monkeypatch):
    """Прогон трекера по звуку: на диске не появляется ничего.

    Сильнее чтения кода: файл будет замечен, как бы он ни назывался и какой
    бы библиотекой ни был записан.
    """
    import numpy as np
    from diarize_live import SpeakerTracker

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)                 # относительные пути пишутся сюда
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()

    before = {p for p in REPO.rglob("*") if ".git" not in p.parts}
    tracker = SpeakerTracker(MODEL, sample_rate=16000)
    rng = np.random.default_rng(0)
    for _ in range(4):
        tracker.label(rng.normal(0, 0.05, 16000 * 2).astype(np.float32))

    assert list(work.iterdir()) == [], f"трекер создал файлы: {list(work.iterdir())}"
    new = {p for p in REPO.rglob("*") if ".git" not in p.parts} - before
    # __pycache__ — забота интерпретатора, а не следы голоса
    new = {p for p in new if "__pycache__" not in p.parts}
    assert not new, f"после диаризации в репозитории появились файлы: {sorted(new)}"


@pytest.mark.skipif(not MODEL.exists(),
                    reason="нет models/diar/embedding.onnx — scripts/get_models.py --diar")
def test_voices_are_forgotten_with_the_tracker():
    """Память о голосах живёт в объекте: новый трекер не знает прошлых встреч."""
    import numpy as np
    from diarize_live import SpeakerTracker

    rng = np.random.default_rng(1)
    chunk = rng.normal(0, 0.05, 16000 * 2).astype(np.float32)

    first = SpeakerTracker(MODEL, sample_rate=16000)
    first.label(chunk)
    assert first.voices >= 1

    second = SpeakerTracker(MODEL, sample_rate=16000)
    assert second.voices == 0, \
        "новый трекер стартовал с уже известными голосами — где-то появилось хранилище"
