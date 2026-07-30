"""Мужчину назвали Анной — и в стенограмме, и в графе, и в поручениях.

Имя опознаётся из разговора: кто-то произнёс «Анна», лёгкая модель решила, что
это имя говорящего. Голос при этом басовитый, но опознание про голос ничего не
знает — оно работает с текстом. Дальше `rename_speaker` переписывает метку
задним числом по всей встрече, и мужчина становится Анной в минутках, в узле
графа и в поручении.

Голос знает об этом достаточно, чтобы такое отклонить. Не «определить пол
человека» — а увидеть, что регистр голоса и род имени уверенно противоречат
друг другу, и в этом случае оставить честное «Собеседник N».

Пороги нарочно осторожные. Мужской диапазон F0 — 85-180 Гц, женский —
165-255, между ними «андрогинная зона» (примерно 145-190), где одна лишь
частота основного тона не решает: там нужны форманты, и исследования
показывают заметное падение точности именно в этой полосе. Поэтому регистр
считается определённым только за её пределами, а внутри — «не знаю», и гейт
не срабатывает вовсе.

Асимметрия ошибок в нашу пользу: ошиблись с регистром — потеряли имя (метка
осталась «Собеседник 2»), а не подписали человека чужим именем. Потерянное имя
дописывается позже, неверное живёт в графе месяцами.

Ничего не хранится: F0 считается по звуку встречи и живёт в памяти ровно
столько же, сколько эмбеддинги голосов, — до конца записи (PRIVACY.md).
"""
import pathlib
import sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import speaker_names  # noqa: E402
import voice_pitch  # noqa: E402

SR = 16000


def tone(f0: float, seconds: float = 0.6, sr: int = SR) -> np.ndarray:
    """Голосоподобный сигнал: основной тон плюс пара гармоник."""
    t = np.arange(int(seconds * sr)) / sr
    wave = (np.sin(2 * np.pi * f0 * t)
            + 0.5 * np.sin(2 * np.pi * 2 * f0 * t)
            + 0.25 * np.sin(2 * np.pi * 3 * f0 * t))
    return (wave / np.max(np.abs(wave))).astype(np.float32)


def test_f0_is_measured_close_enough_to_the_truth():
    for f0 in (110.0, 145.0, 200.0, 240.0):
        got = voice_pitch.estimate_f0(tone(f0), SR)
        assert got is not None, f"не нашли основной тон для {f0} Гц"
        assert abs(got - f0) < 0.1 * f0, f"F0 {got:.0f} вместо {f0:.0f}"


def test_silence_and_noise_have_no_pitch():
    assert voice_pitch.estimate_f0(np.zeros(SR // 2, dtype=np.float32), SR) is None
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.3, SR // 2).astype(np.float32)
    assert voice_pitch.register(voice_pitch.estimate_f0(noise, SR)) is None, \
        "шум не должен получать регистр голоса"


def test_registers_are_named_only_outside_the_ambiguous_band():
    assert voice_pitch.register(115.0) == "low"
    assert voice_pitch.register(225.0) == "high"
    for ambiguous in (150.0, 165.0, 185.0):
        assert voice_pitch.register(ambiguous) is None, \
            f"{ambiguous} Гц — андрогинная зона, здесь F0 не решает"


def test_register_of_a_speaker_is_a_median_not_a_lucky_chunk():
    """Один выкрик выше обычного не должен менять регистр говорящего."""
    chunks = [tone(120), tone(125), tone(118), tone(240)]   # последний — всплеск
    assert voice_pitch.speaker_register(chunks, SR) == "low"


def test_too_little_speech_gives_no_verdict():
    assert voice_pitch.speaker_register([tone(120, seconds=0.15)], SR) is None, \
        "по одному короткому куску регистр не определяют"


def test_name_is_refused_when_voice_and_name_clearly_disagree():
    """Тот самый случай: басовитый голос и женское имя."""
    sample = "[10:00] Я: Анна, привет.\n[10:01] Собеседник: Да, слушаю."
    assert speaker_names.trustworthy_name(
        "Анна", sample=sample, label="Собеседник",
        voice="low", name_gender="female") is None


def test_name_passes_when_voice_and_name_agree():
    sample = "[10:00] Я: Анна, привет.\n[10:01] Собеседник: Да, слушаю."
    assert speaker_names.trustworthy_name(
        "Анна", sample=sample, label="Собеседник",
        voice="high", name_gender="female") == "Анна"


def test_unknown_voice_or_unknown_name_gender_never_blocks():
    """Гейт молчит, когда хоть одна сторона не уверена — иначе он съест
    законные имена: «Саша» и «Женя» бывают и мужскими, и женскими."""
    sample = "[10:00] Я: Саша, привет.\n[10:01] Собеседник: Да."
    for voice, gender in (("low", None), (None, "female"), (None, None),
                          ("low", "unisex"), ("high", "unisex")):
        assert speaker_names.trustworthy_name(
            "Саша", sample=sample, label="Собеседник",
            voice=voice, name_gender=gender) == "Саша", f"{voice}/{gender}"


def test_the_gate_is_off_by_default():
    """Без данных о голосе поведение прежнее — старые вызовы не ломаются."""
    sample = "[10:00] Я: Анна, привет.\n[10:01] Собеседник: Да."
    assert speaker_names.trustworthy_name(
        "Анна", sample=sample, label="Собеседник") == "Анна"
