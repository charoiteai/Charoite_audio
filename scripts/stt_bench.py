#!/usr/bin/env python3
"""Бенч распознавания: сколько знаков пришло не теми.

Качество памяти меряется (`memory_bench.py`), диаризации — тоже
(`diar_bench.py`), а распознавание до сих пор оценивалось на слух. Поэтому
«whisper на китайском хуже» было мнением, а выбор бэкенда — верой.

Здесь считается CER (character error rate) — доля символов, которые пришлось
бы вставить, удалить или заменить, чтобы получить эталон. Для китайского это
уместнее WER: слова там не разделены пробелами, и любое членение — уже
решение алгоритма, а не факт.

    .venv/bin/python scripts/stt_bench.py --backend sensevoice
    .venv/bin/python scripts/stt_bench.py --backend whisper --lang zh
    .venv/bin/python scripts/stt_bench.py --compare          # оба, одним заходом

Замер 20.08 (macOS, голос Tingting, 18 фраз про рабочие встречи):
SenseVoice CER 0.011, Whisper CER 0.047. Ошибается SenseVoice на 2 фразах из
18, Whisper — на 6. Ошибки Whisper содержательные, а не косметические:
«下周以上50点» вместо «下周一上午10点» — время встречи из десяти часов
превратилось в пятьдесят.

Чего этот замер НЕ доказывает (ревью 20.08, DeepSeek). Первый прогон на
четырёх фразах давал 0.050 против 0.147 — и весь разрыв объяснялся не
качеством, а настройкой: у SenseVoice включён ITN, он сам пишет «10点», а
Whisper отдаёт «十点», и эталон цифрами штрафовал его за ПРАВИЛЬНО
распознанное. Теперь CER считается против обеих форм эталона, и цифры упали
втрое. Осталось три оговорки: один синтетический голос (на другой машине
первым говорящим окажется другой), синтез чище живой речи, и классы моделей
неравны — whisper-large-v3-turbo дистиллирован, SenseVoice-Small заточен под
китайский. Отсюда следует «на нашей китайской фикстуре SenseVoice ошибается
реже», а не «Whisper хуже понимает китайский».

Синтетика. Записей встреч в репозитории нет и быть не может — это чужие
разговоры. Фикстура собирается на месте системным синтезатором macOS: голос
читает фразы, эталон известен точно, потому что мы сами его и написали.

Честная оговорка, та же, что у diar_bench: синтезированная речь чище живой —
без перебиваний, эха и комнаты. Это нижняя планка, а не бенчмарк. Движок,
который путается ЗДЕСЬ, на живой встрече будет хуже; обратное неверно, и
выдавать эти цифры за качество на реальных встречах нельзя.
"""
from __future__ import annotations

import argparse
import functools
import pathlib
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

# Фразы рабочей встречи, а не скороговорки: цифры, время, термины — то, на чём
# распознавание ломается и то, ради чего его вообще читают.
# Пара «что произносим — что ждём в ответ». Разделять их пришлось из-за
# нормализации: у бэкенда включён ITN, и «two point eight percent» он
# возвращает как «2.8%» — то есть ровно так, как нужно минуткам. Пока эталон
# был записан прописью, метрика штрафовала за правильное поведение (замер
# 10.08: CER 0.363, из них почти всё — цифры против слов).
PHRASES_ZH = [
    ("我们决定选择 YuPay，费率百分之二点八。", "我们决定选择YuPay，费率2.8%。"),
    ("玛丽亚在七月二十二日之前签好合同。", "玛丽亚在7月22日之前签好合同。"),
    ("下周一上午十点开会讨论集成方案。", "下周一上午10点开会讨论集成方案。"),
    ("认证还没有通过，风险在于时间表。", "认证还没有通过，风险在于时间表。"),
    # Ниже — расширение до статистически осмысленного объёма (ревью 20.08:
    # на четырёх фразах уровень значимости 0.05 недостижим в принципе, а
    # удаление одной фразы переворачивает ранжирование движков).
    ("预算增加了百分之十五，需要总监批准。", "预算增加了15%，需要总监批准。"),
    ("服务器迁移安排在十月三号的晚上。", "服务器迁移安排在10月3号的晚上。"),
    ("这个功能推迟到第四季度再上线。", "这个功能推迟到第四季度再上线。"),
    ("客户要求延长试用期到三十天。", "客户要求延长试用期到30天。"),
    ("我们需要重新评估这个方案的成本。", "我们需要重新评估这个方案的成本。"),
    ("测试环境昨天下午两点崩溃了。", "测试环境昨天下午2点崩溃了。"),
    ("请把会议纪要发给所有的参与者。", "请把会议纪要发给所有的参与者。"),
    ("接口文档还差两个章节没有写完。", "接口文档还差2个章节没有写完。"),
    ("第一阶段的目标是降低百分之二十的延迟。", "第一阶段的目标是降低20%的延迟。"),
    ("安全审计报告下周三之前提交。", "安全审计报告下周三之前提交。"),
    ("新员工的培训计划已经确定下来了。", "新员工的培训计划已经确定下来了。"),
    ("这次故障影响了大约五千个用户。", "这次故障影响了大约5000个用户。"),
    ("我们把并发数从一百提高到五百。", "我们把并发数从100提高到500。"),
    ("产品经理希望在发布前再做一轮评审。", "产品经理希望在发布前再做一轮评审。"),
]
PHRASES_EN = [
    ("We decided to go with YuPay, the fee is two point eight percent.",
     "We decided to go with YuPay, the fee is 2.8%."),
    ("Maria signs the contract before July twenty second.",
     "Maria signs the contract before July 22nd."),
    ("The next meeting is Monday at ten in the morning.",
     "The next meeting is Monday at 10 in the morning."),
]
LOCALES = {"zh": "zh_CN", "en": "en_US"}


# Пустой aiff от несклачанного голоса весит ~4.8 КБ заголовка. Реальная
# фраза — десятки килобайт; порог с запасом отделяет одно от другого.
_SILENT_AIFF = 8_000


def voices_for(lang: str) -> list[str]:
    """Все системные голоса языка — из `say -v '?'`, а не из константы.

    Первая версия задавала голос строкой «Eddy (Chinese (China mainland))».
    На русской системе он называется иначе, `say` голос не нашёл, молча взял
    английский по умолчанию и наговорил китайский текст латиницей. Ошибки при
    этом не было — был CER 1.0 и вывод «右背。» вместо фразы.
    """
    locale = LOCALES.get(lang, "en_US")
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                             check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    found: list[str] = []
    for line in out.splitlines():
        if f" {locale} " not in line and not line.split("#")[0].rstrip().endswith(locale):
            continue
        # «Eddy (Китайский (Китай континентальный)) zh_CN  # …» → «Eddy»
        name = line.split("(")[0].strip() if "(" in line else line.split()[0]
        if name and name not in found:
            found.append(name)
    return found


@functools.lru_cache(maxsize=8)
def voice_for(lang: str) -> str | None:
    """Первый голос языка, который РЕАЛЬНО говорит.

    Голос может быть в списке и при этом не скачан: `say` отрабатывает с
    кодом 0 и пишет файл из одного заголовка. Скрипт брал первый по списку
    (у китайского это Eddy — как раз такой), три фразы из четырёх
    пропускались, четвёртая давала CER 1.0 — и «бенч» показывал ничью двух
    движков на пустом месте. Проверяем делом: короткая фраза должна дать
    звук (замер 20.08: Eddy и Flo молчат, Tingting, Meijia и Sinji говорят).
    """
    probe = pathlib.Path(tempfile.gettempdir()) / "stt_bench_probe.aiff"
    for name in voices_for(lang):
        try:
            subprocess.run(["say", "-v", name, "-o", str(probe), "你好" if lang == "zh" else "hello"],
                           check=True, capture_output=True)
            if probe.exists() and probe.stat().st_size > _SILENT_AIFF:
                return name
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        finally:
            probe.unlink(missing_ok=True)
    return None


def synth(text: str, lang: str, dest: pathlib.Path) -> bool:
    """Наговорить фразу системным синтезатором в 16 кГц mono wav."""
    aiff = dest.with_suffix(".aiff")
    voice = voice_for(lang)
    if not voice:
        names = ", ".join(voices_for(lang)) or "ни одного"
        print(f"нет ГОВОРЯЩЕГО голоса для {lang} (в списке: {names}) — "
              "скачайте его в Системных настройках → Универсальный доступ → "
              "Речь → Системный голос → Управление голосами")
        return False
    try:
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text],
                       check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(aiff), str(dest)], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"синтез не удался ({voice}): {e}")
        return False
    finally:
        aiff.unlink(missing_ok=True)
    return dest.exists()


def cer_best(spoken: str, expected: str, hypothesis: str) -> float:
    """Лучший CER против ОБЕИХ форм эталона: цифровой и прописной.

    Иначе бенч сравнивает не движки, а их настройки: у SenseVoice включён
    ITN — он сам пишет «10点», — а Whisper отдаёт сырой декод «十点». Эталон
    записан цифрами, и Whisper штрафовался за то, что распознал ПРАВИЛЬНО,
    но не переписал числа. Одна такая фраза даёт CER 0.125 — больше, чем
    весь наблюдавшийся разрыв между движками (ревью 20.08, DeepSeek).
    """
    return min(cer(expected, hypothesis), cer(spoken, hypothesis))


def cer(reference: str, hypothesis: str) -> float:
    """Расстояние Левенштейна по символам, нормированное на длину эталона.

    Пунктуация и пробелы выброшены: STT расставляет их по-своему, и считать
    это ошибкой распознавания — завышать метрику на ровном месте.
    """
    import unicodedata

    def norm(s: str) -> str:
        # NFKC: полноширинные цифры и латиница («０», «Ａ») иначе не равны
        # обычным, и движок получал бы штраф за форму знака.
        s = unicodedata.normalize("NFKC", s)
        out: list[str] = []
        for i, c in enumerate(s):
            if c == "." and 0 < i < len(s) - 1 and s[i - 1].isdigit() and s[i + 1].isdigit():
                out.append(c)          # «2.8» ≠ «28»: десятичная точка — смысл,
                continue               # а не пунктуация (ставка 2.8% против 28%)
            if not unicodedata.category(c).startswith(("P", "Z")):
                out.append(c.lower())
        return "".join(out)

    ref, hyp = norm(reference), norm(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def run(backend: str, lang: str, phrases: list[tuple[str, str]]) -> tuple[float, int]:
    """Прогнать фразы через бэкенд. Возвращает средний CER и число фраз."""
    from stt import STT

    cfg = {"stt": {"backend": backend, "language": lang,
                   "whisper_model": "mlx-community/whisper-large-v3-turbo",
                   "gigaam_model": "gigaam-v3-e2e-rnnt",
                   "parakeet_model": "mlx-community/parakeet-tdt-0.6b-v3"},
           "sufler": {}}
    engine = STT(cfg)
    import numpy as np

    total, done = 0.0, 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, (spoken, expected) in enumerate(phrases):
            wav = pathlib.Path(tmp) / f"p{i}.wav"
            if not synth(spoken, lang, wav):
                continue
            with wave.open(str(wav), "rb") as w:
                rate = w.getframerate()
                audio = np.frombuffer(w.readframes(w.getnframes()),
                                      dtype=np.int16).astype("float32") / 32768.0
            # Тишина — не плохое распознавание, а ненаговоренная фраза.
            # macOS показывает голоса, которые ещё не скачаны: `say` отдаёт
            # заголовок без звука и не жалуется, а бенч насчитывал CER 1.0 и
            # выглядело это как «модель не понимает китайский».
            if audio.size < rate * 0.2 or float(np.abs(audio).max()) < 1e-4:
                print(f"  пропуск: голос «{voice_for(lang)}» не наговорил фразу "
                      "(голос виден в списке, но не скачан — Системные настройки "
                      "→ Универсальный доступ → Речь)")
                continue
            text = engine.transcribe(audio, rate)
            score = cer_best(spoken, expected, text)
            total += score
            done += 1
            print(f"  CER {score:5.3f}  ← {text[:58]}")
    return (total / done if done else 1.0), done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backend", default="sensevoice",
                    help="sensevoice | whisper | parakeet | gigaam")
    ap.add_argument("--lang", default="zh", choices=sorted(LOCALES),
                    help="язык фикстуры (zh — китайский, en — английский)")
    ap.add_argument("--compare", action="store_true",
                    help="прогнать sensevoice и whisper подряд и сравнить")
    args = ap.parse_args()

    phrases = PHRASES_ZH if args.lang == "zh" else PHRASES_EN
    backends = ["sensevoice", "whisper"] if args.compare else [args.backend]
    results: dict[str, float] = {}
    for backend in backends:
        print(f"\n{backend} · {args.lang} · {len(phrases)} фраз:")
        try:
            score, done = run(backend, args.lang, phrases)
        except Exception as e:  # noqa: BLE001 — бенч не должен падать трейсбеком
            print(f"  не запустился: {e}")
            continue
        if not done:
            print("  ни одной фразы не синтезировалось")
            continue
        results[backend] = score
        print(f"  средний CER: {score:.3f}")

    if len(results) > 1:
        best = min(results, key=results.get)
        print(f"\nлучше на этой фикстуре: {best} "
              f"({', '.join(f'{k} {v:.3f}' for k, v in results.items())})")
    print("\nСинтезированная речь чище живой: это нижняя планка, не бенчмарк.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
