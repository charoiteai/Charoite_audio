"""Оффлайн-расшифровка аудиозаписи в стенограмму конвейера суфлёра.

Запуск: .venv/bin/python src/transcribe_file.py <файл.m4a|wav> [ЧЧММ] [ГГГГ-ММ-ДД]
m4a → wav 16кГц (afconvert, нативный macOS) → GigaAM сегментами 25с
с перекрытием → склейка с дедупом швов → transcripts/<дата>_<ЧЧММ>.md →
дальше обычный путь: graph_updater (тема, граф, разбор, Opus-ревизия).
"""
from __future__ import annotations

import datetime as dt
import atexit
import shutil
import pathlib
import subprocess
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from stt import AFCONVERT_TIMEOUT, STT  # noqa: E402
from transcript import Transcript, is_noise  # noqa: E402

from charoite_paths import harden_umask, resolve_root
from config_loader import load_user_or_example


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Снимок на уровне модуля считался при импорте, то есть раньше, чем точка
    входа успевала назвать корень: половина процесса жила в названном корне,
    половина — в выведенном из положения файла, и расхождение было немым
    (замер 21.09, №329).
    """
    return resolve_root(__file__)

SEG_S = 25.0
OVERLAP_S = 1.0



def _scratch_dir() -> pathlib.Path:
    """Временная папка, которая ГАРАНТИРОВАННО исчезнет вместе с процессом.

    Сюда кладётся 16-кГц WAV всей встречи (трёхчасовая — 345 МБ). Раньше это
    был просто mkdtemp без уборки: копия полного аудио оставалась в
    /var/folders до перезагрузки, а часто и дольше. Из неё извлекаются
    голосовые эмбеддинги, и про неё не знает ретеншн record_keep_days —
    то есть обещание «записи временны» обходилось незаметной копией.
    """
    d = pathlib.Path(tempfile.mkdtemp(prefix="charoite-"))
    atexit.register(shutil.rmtree, d, True)
    return d

STT_RATE = 16000   # контракт STT.transcribe — float32 mono 16 кГц (stt.py)


def _afconvert(src: pathlib.Path, out: pathlib.Path) -> pathlib.Path:
    """Свести что угодно к моно 16 бит 16 кГц; потолок — против зависшего входа."""
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{STT_RATE}", "-c", "1",
                    str(src), str(out)], check=True, capture_output=True,
                   timeout=AFCONVERT_TIMEOUT)
    return out


def wav_is_mono_16bit(path: pathlib.Path, rate: int = STT_RATE) -> bool:
    """WAV уже в формате STT: моно, 16 бит, нужная частота. Не разобрали —
    False: пусть сводит afconvert, он и скажет, что с файлом не так."""
    try:
        with wave.open(str(path), "rb") as w:
            return (w.getnchannels() == 1 and w.getsampwidth() == 2
                    and w.getframerate() == rate)
    except (wave.Error, EOFError, OSError):
        return False


def to_wav16k(src: pathlib.Path, pcm_rate: int = STT_RATE) -> pathlib.Path:
    if src.suffix.lower() == ".wav" and wav_is_mono_16bit(src):
        return src
    # Стерео или не 16 кГц: до 13.09 такой WAV отдавался как есть — стерео
    # читалось моно двойной скорости, 44,1 кГц уходило в модель под своей
    # частотой, стенограмма выходила мусором без единой ошибки (аудит 13.09,
    # DS I3 / GLM I1). Сводим тем же afconvert, что и m4a.
    if src.suffix.lower() == ".pcm":  # сырая запись AudioHub после крэша: s16le mono
        out = _scratch_dir() / "rec.wav"
        with wave.open(str(out), "wb") as w, src.open("rb") as f:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(pcm_rate)  # из audio.samplerate конфига, не хардкод
            while chunk := f.read(1 << 20):
                w.writeframes(chunk)
        if pcm_rate == STT_RATE:
            return out
        # крэш-запись на частоте конфига (audio.samplerate ≠ 16000): гейт ниже
        # её не пропустит, сводим тем же afconvert (DS r1 I1 / GLM M4 по #555)
        return _afconvert(out, _scratch_dir() / "rec16k.wav")
    return _afconvert(src, _scratch_dir() / "rec.wav")


def main():
    harden_umask()  # данные встреч — только владельцу (аудит 16.08)
    src = pathlib.Path(sys.argv[1]).expanduser()
    if not src.exists():
        sys.exit(f"нет файла: {src}")
    cfg = load_user_or_example(_root())
    stt = STT(cfg)
    wav = to_wav16k(src, pcm_rate=int(cfg["audio"]["samplerate"]))
    with wave.open(str(wav), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != STT_RATE:
            sys.exit(f"{wav.name}: после сведения ожидался моно 16-бит WAV {STT_RATE} Гц, "
                     f"а не {w.getnchannels()} кан. × {w.getsampwidth() * 8} бит @ {w.getframerate()} Гц")
        sr = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    dur = len(audio) / sr
    print(f"{src.name}: {dur/60:.1f} мин @ {sr} Гц")

    # штамп: время записи файла (когда встреча была), не время расшифровки
    mt = dt.datetime.fromtimestamp(src.stat().st_mtime)
    # Вторым аргументом — время как есть: «1258», «125812» или «125812-1»
    # (импорт второй записи в занятую минуту, карточка №41) — штамп
    # склеивается без разбора, файл получает ровно то имя, которое ждёт
    # import_meeting.
    hhmm = sys.argv[2] if len(sys.argv) > 2 else f"{mt:%H%M}"
    # дата встречи третьим аргументом: у старой записи mtime может быть
    # датой копирования, а recency и имя файла живут на дате встречи
    day = sys.argv[3] if len(sys.argv) > 3 else f"{mt:%Y-%m-%d}"
    stamp = f"{day}_{hhmm}"

    out_dir = _root() / cfg["log"]["transcripts_dir"]
    tpath = out_dir / f"{stamp}.md"
    parts: list[str] = []
    prev = ""
    seg = int(SEG_S * sr)
    ov = int(OVERLAP_S * sr)
    t0 = dt.datetime.now()
    for i, start in enumerate(range(0, len(audio), seg - ov)):
        chunk = audio[start:start + seg]
        if len(chunk) < sr:  # хвост меньше секунды
            break
        text = stt.transcribe(chunk, sr).strip()
        if not text or is_noise(text):
            continue
        text = Transcript._cut_overlap(prev, text) if prev else text
        if text:
            parts.append(text)
            prev = parts[-1]
        done = min(start + seg, len(audio)) / len(audio)
        print(f"\r  {done*100:3.0f}% ({i+1} сегм.)", end="", flush=True)
    took = (dt.datetime.now() - t0).total_seconds()
    print(f"\nраспознано за {took:.0f}с (RTF {dur/max(took,0.1):.0f}x)")

    # Размер в шапке: по нему import_meeting отличает вторую запись с тем
    # же именем (Recording.m4a у диктофона) от повтора той же записи.
    body = (f"# Встреча {stamp} — запись {src.name} ({src.stat().st_size} Б)\n\n"
            "**Голос** [запись, спикеры не разделены]:\n" + " ".join(parts) + "\n")
    tpath.write_text(body, encoding="utf-8")
    print(f"стенограмма: {tpath} ({len(body)} зн.)")
    print("дальше: .venv/bin/python src/graph_updater.py", tpath)


if __name__ == "__main__":
    main()
