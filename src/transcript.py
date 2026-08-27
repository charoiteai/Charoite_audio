"""Dependency-light transcript state; runtime must not import legacy ``main.py``."""
from __future__ import annotations

import datetime as dt
import difflib
import pathlib
import re
import threading

import meeting_stamp

# Легаси эпохи whisper: он галлюцинировал на тишине готовыми фразами из
# субтитров («продолжение следует…»), и мы вырезали их списком. GigaAM (Сбер),
# наш STT с 16.07, на тишине выдаёт blank — замер 23.07: за 2174 реплики фильтр
# не сработал ни разу, похожего мусора в стенограммах нет. Список оставлен как
# дешёвая страховка на случай отката к whisper; расширять его хардкодом НЕ надо
# — если GigaAM когда-то начнёт лить мусор, это будет другой мусор, и ловить
# его нужно классификатором (gen_hint уже видит реплику целиком), а не
# дописыванием строк сюда.
NOISE = {"продолжение следует...", "субтитры делал dimatorzok",
         "спасибо за просмотр!", "спасибо за просмотр"}


BLOCK_RE = re.compile(
    r"^\*\*(?P<spk>.+?)\*\*\s*\[(?P<t0>\d{1,2}:\d{2}(?::\d{2})?)"
    r"(?:\s*[–—-]\s*(?P<t1>\d{1,2}:\d{2}(?::\d{2})?))?\]\s*:\s*$",
    re.M | re.UNICODE)

def parse_blocks(text: str) -> list[dict]:
    """Разобрать стенограмму обратно в блоки — рядом с тем, кто их пишет.

    Формат заголовка знает ровно одно место в проекте: `_render` его строит,
    `parse_blocks` разбирает. Потребителям (графу, провенансу) не нужно
    угадывать регулярками, где кончается чужая реплика: два обхода по PR #438
    подряд дали Critical именно на самодельных эвристиках — сначала заголовок
    не находился вовсе, потом находился ЧУЖОЙ, из предыдущей реплики.

    Возвращает список словарей: speaker, time (начало реплики), start и end —
    смещения текста реплики в исходной строке. Для внешних стенограмм, писанных
    не нами, список будет пустым: это честный сигнал «формат не наш».
    """
    out: list[dict] = []
    for m in BLOCK_RE.finditer(text):
        out.append({"speaker": m.group("spk").strip(),
                    "time": m.group("t0"),
                    "start": m.end(),
                    "end": len(text)})
        if len(out) > 1:
            out[-2]["end"] = m.start()
    return out


class Transcript:
    """Стенограмма репликами по спикерам: соседние чанки одного голоса склеиваются.

    Файл перезаписывается целиком (реплики + заметки ко-мышления в конце).
    """

    # Пока говорит тот же спикер — клеим в ОДИН абзац; новый блок только после
    # смены спикера или совсем длинной паузы (иначе стенограмма рвётся на строчки).
    SPLIT_GAP = 180.0

    def __init__(self, out_dir: pathlib.Path):
        out_dir.mkdir(exist_ok=True)
        # Секунды в штампе и отказ писать поверх — не косметика. Авто-рестарт
        # после сбоя поднимает демон через 2 секунды, то есть в 58 случаях из 60
        # внутри той же минуты: со штампом до минут новый процесс открывал файл
        # прошлой встречи и затирал её первым же _save(), а .pcm обнулял open("wb").
        # Час разговора исчезал вместе со страховочной записью.
        # Формат — в meeting_stamp: этим же именем демон называет записи
        # каналов, а rebuild_transcript их ищет. Своя strftime здесь была бы
        # четвёртым независимым представлением одного и того же имени.
        stamp = meeting_stamp.now()
        self.path = out_dir / f"{stamp}.md"
        n = 1
        while self.path.exists():
            self.path = out_dir / f"{stamp}-{n}.md"
            n += 1
        self.stamp = self.path.stem
        self._title = f"# Встреча {stamp}"
        # блок: [t_start, t_last, speaker, text]
        self._blocks: list[list] = []
        self._notes: list[str] = []
        self._names: dict[str, str] = {}  # канальная метка → опознанное имя
        self._prev_chunk: dict[str, str] = {}  # спикер → последний чанк (дедуп швов)
        self._participants: list[str] = []  # групповая встреча: кто звучал
        self._lock = threading.Lock()
        self._save()

    # Соседние чанки перекрываются, и STT распознаёт общий кусок ПО-РАЗНОМУ:
    # «Вот тут, на самом деле…» / «Тут, на самом деле…», «в магине» / «в кабинете».
    # Точное сравнение слов такое не ловит — сверяем нечётко (замер 22.07: склейки
    # были в 41 реплике из 2556, в худшей встрече 28 из 212).
    _DUPLICATE_RATIO = 0.8  # ниже 0.75 начинает резать живую речь
    _MIN_DUP_WORDS = 3  # «Да. Да.» — нормальная речь, не склейка

    @staticmethod
    def _norm_words(text: str) -> list[str]:
        return re.findall(r"[\w-]+", text.lower())

    @classmethod
    def _similar(cls, a: list[str], b: list[str]) -> float:
        """Похожесть по словам, а не по буквам: одна кривая буква не рушит счёт."""
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()

    @classmethod
    def _cut_overlap(cls, prev: str, new: str) -> str:
        """Режет повтор шва: хвост предыдущего чанка обычно повторяется в начале нового."""
        pw, nw = cls._norm_words(prev), cls._norm_words(new)
        if not pw or not nw:
            return new
        words = new.split()

        # Границу перекрытия не угадываем по длине, а вычисляем по совпадающим
        # кускам. Берём все блоки, а не самый длинный: одно расслышанное иначе
        # слово («будет, то очень хорошо» / «будет хорошо») рвёт совпадение
        # надвое, и по одному куску конец перекрытия не найти.
        blocks = [
            b
            for b in difflib.SequenceMatcher(None, pw, nw).get_matching_blocks()
            if b.size
        ]
        if blocks:
            first, last = blocks[0], blocks[-1]
            matched = sum(b.size for b in blocks)
            # Склейка выглядит так: общее начинается в НАЧАЛЕ нового чанка (STT
            # порой лепит спереди «а вот») и дотягивается до КОНЦА предыдущего.
            # Совпадение в середине — обычный повтор слова в живой речи.
            at_start = first.b <= 2
            at_end = last.a + last.size >= len(pw) - 1
            # 0.8, а не 0.6: «посмотреть на бюджет» / «посмотреть на сроки» —
            # разные мысли с общим началом, их совпадение как раз 0.75.
            enough = (
                matched >= cls._MIN_DUP_WORDS
                and matched >= 0.8 * min(len(pw), len(nw))
            )
            if enough and at_start and at_end:
                rest = words[last.b + last.size:]
                # Огрызок в одно слово — хвост неверно расслышанной концовки
                # («…нету адреса» / «…нету адреса»). Мусор, а не прирост.
                if len(rest) <= 1 and cls._similar(pw, nw) >= cls._DUPLICATE_RATIO:
                    return ""
                return " ".join(rest)

        # Короткий хвост совпал точно — прежнее поведение, для «на» и «что»
        for k in range(min(8, len(pw), len(nw)), 1, -1):
            if pw[-k:] == nw[:k]:
                return " ".join(words[k:])
        return new

    def add(self, text: str, speaker: str | None = None) -> str | None:
        """Добавляет чанк; возвращает реально добавленный текст (после дедупа) или None."""
        now = dt.datetime.now()
        spk = speaker or "—"
        with self._lock:
            spk = self._names.get(spk, spk)
            # шов перекрытия чанков живёт ВНУТРИ канала: сверяем с последним текстом
            # этого же спикера, а не с чужим блоком (иначе дубль слов на смене голоса)
            prev = self._prev_chunk.get(spk, "")
            if prev:
                text = self._cut_overlap(prev, text)
                if not text:
                    return None
            self._prev_chunk[spk] = text
            if self._blocks:
                b = self._blocks[-1]
                same = b[2] == spk and (now - b[1]).total_seconds() < self.SPLIT_GAP
                if same:
                    b[3] = f"{b[3]} {text}"
                    b[1] = now
                else:
                    self._blocks.append([now, now, spk, text])
            else:
                self._blocks.append([now, now, spk, text])
        self._save()
        return text

    def note(self, line: str):
        """Заметка ко-мышления (📌/💎/💭) — в конец файла, отдельным разделом."""
        with self._lock:
            self._notes.append(f"{dt.datetime.now():%H:%M} {line}")
        self._save()

    def display_name(self, speaker: str) -> str:
        with self._lock:
            return self._names.get(speaker, speaker)

    def rename_speaker(self, old: str, new: str):
        """Опознали имя из разговора: заменить метку задним числом во всех блоках."""
        with self._lock:
            self._names[old] = new
            for b in self._blocks:
                if b[2] == old:
                    b[2] = new
        self._save()

    def set_participants(self, names: list[str]):
        """Групповая встреча: список звучавших имён — в шапку стенограммы."""
        with self._lock:
            self._participants = list(names)
        self._save()

    def _render(self) -> str:
        parts = [self._title]
        if self._participants:
            parts.append(f"Участники (звучали в разговоре): {', '.join(self._participants)}")
        parts.append("")
        for t0, t1, spk, text in self._blocks:
            span = f"{t0:%H:%M}" if f"{t0:%H:%M}" == f"{t1:%H:%M}" else f"{t0:%H:%M}–{t1:%H:%M}"
            parts.append(f"**{spk}** [{span}]:")
            parts.append(text)
            parts.append("")
        if self._notes:
            parts.append("---")
            parts.append("## Ко-мышление (📌 КТ · 💎 факты · 💭 мысли)")
            parts.extend(f"> {n}" for n in self._notes)
        return "\n".join(parts) + "\n"

    def _save(self):
        # Под локом целиком: _save дёргают 4 треда (stt/think/deep/name), а tmp-путь
        # один — конкурентные write_text мешали байты, второй replace ловил
        # FileNotFoundError и убивал stt_loop (профиль инцидента «стенограмма молчит»)
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self._render(), encoding="utf-8")
            tmp.replace(self.path)

    def tail(self, max_chars: int) -> str:
        with self._lock:
            out: list[str] = []
            total = 0
            for t0, _t1, spk, text in reversed(self._blocks):
                s = f"[{t0:%H:%M}] {spk}: {text}"
                total += len(s) + 1
                if total > max_chars:
                    if not out:  # монолог длиннее лимита: отдать усечённый хвост,
                        out.append(s[-max_chars:])  # иначе ⚡/☁️ молча глотали вопрос
                    break
                out.append(s)
        return "\n".join(reversed(out))

    def full(self) -> str:
        with self._lock:
            return "\n".join(
                f"[{t0:%H:%M}] {spk}: {text}"
                for t0, _t1, spk, text in self._blocks
            )

    def last(self) -> str:
        with self._lock:
            return self._blocks[-1][3] if self._blocks else ""

    def last_block(self) -> tuple[int, dt.datetime, str, str] | None:
        """(index, t_last, спикер, текст) последнего блока — для семантической разметки."""
        with self._lock:
            if not self._blocks:
                return None
            i = len(self._blocks) - 1
            _t0, t1, spk, text = self._blocks[i]
            return i, t1, spk, text

    def update_block_text(self, idx: int, old_text: str, new_text: str) -> bool:
        """Заменить текст блока, только если он не дописался с момента снапшота."""
        with self._lock:
            if 0 <= idx < len(self._blocks) and self._blocks[idx][3] == old_text:
                self._blocks[idx][3] = new_text
                ok = True
            else:
                ok = False
        if ok:
            self._save()
        return ok

    def notes(self) -> list[str]:
        with self._lock:
            return list(self._notes)

    def names(self) -> dict[str, str]:
        """Опознанные за встречу имена: «Собеседник N» → «Алексей».

        Нужны пересборке: без них rebuild диаризует заново и заново гадает
        имена, теряя всё, что демон выяснил за час разговора.
        """
        with self._lock:
            return dict(self._names)
