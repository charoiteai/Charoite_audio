"""Живая диаризация собеседников: несколько голосов в одном BlackHole-канале.

На каждый речевой чанк канала — ERes2Net-эмбеддинг (модель уже в models/diar,
~20-50мс на CPU) → косинус к центроидам известных голосов: похож — тот же
голос (центроид дообучается), нет — новый. Стенограмма получает метки
«Собеседник 1/2/3» — абзацы по говорящим вместо слитной каши.

Консервативно: короткий/тихий чанк или неуверенность → None, демон оставляет
общую метку «Собеседник» — хуже текущего поведения не становится. Имена
голосам сопоставляет оффлайн-диаризация после встречи (*_спикеры.md).
"""
from __future__ import annotations

import dataclasses
import pathlib

import numpy as np


@dataclasses.dataclass(frozen=True)
class Piece:
    """Кусок чанка для отдельного распознавания.

    start/end — pad-окно для STT (сэмплы), raw_start/raw_end — сырые границы
    речи без запаса: по ним считается высота голоса, чтобы в оценку не попал
    сосед из padding (ревью 15.08). voice — номер голоса (1..N).
    """
    start: int
    end: int
    voice: int
    raw_start: int
    raw_end: int


@dataclasses.dataclass(frozen=True)
class SplitResult:
    """Итог позиционной раскладки чанка — трёхсостоянный контракт (ревью 15.08).

    pieces == None — раскладка ничего не решила (ошибка, нет модели, ни
    одного назначенного сегмента) ЛИБО единственный голос покрывает чанк без
    исключённых кусков: распознавать чанк целиком с меткой main — честный
    fail-open или быстрый путь.
    pieces == []  — речь была, но вся сознательно исключена политикой
    (придержанный хвост, микро-куски): чанк НЕ распознавать, иначе STT целого
    чанка вернёт слова исключённых и подпишет их меткой main — ровно та
    подмена автора, от которой раскладку заводили.
    pieces == [..] — распознавать окна, даже если голос в них один.
    """
    pieces: list[Piece] | None
    main: int | None


def jobs_for(res: "SplitResult | None", chunk: np.ndarray) \
        -> list[tuple[np.ndarray, int, np.ndarray | None]] | None:
    """План распознавания чанка по трёхсостоянному контракту SplitResult.

    Чистая функция — стык «раскладка → демон» дважды ловил дыры на ревью
    15.08, поэтому тестируется без потоков и настоящего STT. None — чанк не
    распознавать вовсе (вся речь исключена политикой). Иначе список заданий
    (кусок для STT, голос, сырой кусок для оценки высоты): голос -1 — метка
    канала (раскладка упала или молчит — повторный вызов трекера учил бы
    центроиды тем же звуком дважды), положительный — номер голоса трекера.
    """
    if res is None:  # split бросил исключение: канальная метка, не voice_label
        return [(chunk, -1, None)]
    if res.pieces is not None and not res.pieces:
        return None
    if res.pieces:
        return [(chunk[p.start:p.end], p.voice, chunk[p.raw_start:p.raw_end])
                for p in res.pieces]
    if res.main is not None:
        return [(chunk, res.main, chunk)]
    return [(chunk, -1, None)]


def plan_pieces(raw: list[tuple[float, float, int | None]], chunk_len: int,
                sr: int, *, min_stt: float = 1.0, pad: float = 0.25,
                gap: float = 0.4, edge_eps: float = 0.05,
                step_s: float = 2.5) -> tuple[
                    list[tuple[float, float, int, float, float]],
                    bool,
                    list[tuple[float, float, int]]]:
    """Спланировать окна STT по назначенным сегментам. Чистая логика — без ONNX.

    raw: (start_s, end_s, voice|None) в секундах от начала чанка. Возвращает
    (окна: pad-границы + голос + сырые границы речи, придержан ли правый
    хвост, куски после придержки — по ним трекер решает, кого дообучать).

    Правила — против «микро-меток» и дублей на перекрытии чанков:
    - придерживается ТОЛЬКО сегмент, обрезанный правым краем (конец в edge_eps
      от границы) и живущий целиком в зоне перекрытия (start >= step_s):
      такой сегмент следующий чанк принесёт полностью. Сегмент, начавшийся
      раньше зоны перекрытия, придерживать нельзя — следующий чанк повторит
      лишь последние chunk-step секунд, и «задержка» стала бы потерей реплики
      (ревью 15.08); он выпускается обрезанным, полсекундный дубль на стыке
      дешевле потерянных слов;
    - соседние сегменты одного голоса с зазором < gap сливаются в одно окно;
    - окно короче min_stt своего голоса не получает: приписать полсекундное
      «да» соседу по времени значило бы подменить автора (правило коротких
      сирот оффлайн-прохода), поэтому такой кусок просто не распознаётся;
    - окна расширяются на pad с краёв (обрезанные фонемы), но не за границы
      чанка и не дальше середины зазора с СОСЕДНИМ РЕЧЕВЫМ КУСКОМ ДРУГОГО
      голоса — иначе один и тот же участок распознаётся дважды под разными
      людьми (ревью 15.08).
    """
    total_s = chunk_len / sr
    deferred = False
    kept: list[tuple[float, float, int]] = []
    for start, end, voice in raw:
        if voice is None:
            continue
        if end > total_s - edge_eps and start >= step_s - edge_eps:
            deferred = True
            continue
        kept.append((start, end, voice))
    kept.sort()

    merged: list[list[float | int]] = []
    for start, end, voice in kept:
        if merged and merged[-1][2] == voice and start - merged[-1][1] < gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, voice])

    # Барьеры для padding — ВСЕ сырые куски, включая придержанные и куски без
    # назначения: их звук физически в чанке, и pad чужого окна не должен его
    # накрыть (ревью 15.08 ×3). Вложенный чужак (короткое «угу» внутри
    # монолога) режет кусок монолога на части ДО оконной логики — иначе два
    # midpoint-обрезания схлопывали всё окно монолога.
    barriers = list(raw)

    def cut_out_nested(start: float, end: float, voice: int) -> list[tuple[float, float]]:
        parts = [(start, end)]
        for s2, e2, v2 in barriers:
            # режем только по НАЗНАЧЕННОМУ чужаку: неопознанный шум (None)
            # не должен дырявить монолог — он остаётся барьером для краёв
            if v2 is None or v2 == voice:
                continue
            nxt: list[tuple[float, float]] = []
            for ps, pe in parts:
                if s2 > ps and e2 < pe:      # чужак строго внутри куска
                    nxt.extend([(ps, s2), (e2, pe)])
                else:
                    nxt.append((ps, pe))
            parts = nxt
        return [(ps, pe) for ps, pe in parts if pe - ps >= min_stt]

    big: list[tuple[float, float, int]] = []
    for s, e, v in merged:
        if e - s < min_stt:
            continue
        big.extend((ps, pe, v) for ps, pe in cut_out_nested(s, e, v))

    windows: list[list[float]] = []
    for start, end, voice in big:
        a, b = start - pad, end + pad
        for s2, e2, v2 in barriers:
            if v2 == voice:
                continue
            if e2 <= start:
                a = max(a, (e2 + start) / 2)
            elif s2 >= end:
                b = min(b, (end + s2) / 2)
            elif s2 > start and e2 < end:
                # вложенный барьер окно не трогает: назначенный чужак уже
                # вырезан cut_out_nested, а вложенный шум (None) монолог не
                # дырявит — двойной midpoint-сдвиг схлопывал окно (ревью ×5)
                continue
            elif s2 <= start and e2 >= end:  # накрыл целиком: окна не будет
                a = b = (max(start, s2) + min(end, e2)) / 2
            else:  # частичное пересечение: делим спорное пополам
                mid = (max(start, s2) + min(end, e2)) / 2
                if s2 > start:      # чужак начался внутри моего куска
                    b = min(b, mid)
                else:               # чужак кончился внутри моего куска
                    a = max(a, mid)
        a, b = max(0.0, a), min(total_s, b)
        if b - a <= 1e-9:
            continue
        # сырые границы подрезаются окном: pitch не должен слышать спорную
        # зону, отрезанную midpoint-правилом (ревью 15.08 ×3)
        rs, re_ = max(start, a), min(end, b)
        if windows and windows[-1][2] == voice and a <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], b)
            windows[-1][4] = max(windows[-1][4], re_)
        else:
            windows.append([a, b, voice, rs, re_])
    return ([(float(a), float(b), int(v), float(rs), float(re))
             for a, b, v, rs, re in windows], deferred, kept)


def tracker_kind(seg_model: pathlib.Path, emb_model: pathlib.Path) -> str | None:
    """Каким трекером работать: «segments», «chunks» или никаким.

    Сегментация меняет качество разительно (замер на синтетическом диалоге:
    DER 0.246 против 0.725 и четыре голоса против одного), но без неё режим по
    чанкам остаётся рабочим — просто слабее. Поэтому выбор, а не отказ.
    """
    if not emb_model.exists():
        return None
    return "segments" if seg_model.exists() else "chunks"


def availability_note(enabled: bool, model_path: pathlib.Path,
                      seg_path: pathlib.Path | None = None) -> str | None:
    """Что сказать пользователю про живую диаризацию. None — она работает.

    Модели в поставку не входят, а `live_diarize` включён по умолчанию:
    «модели нет» — это состояние сразу после установки, а не авария. Молча
    отдать метки по каналам вместо обещанных «Собеседник 1/2/…» хуже, чем
    сказать вслух: человек видит слитную кашу и не знает, чинить ему что-то
    или так и задумано.

    Отдельно про упрощённый режим: без модели сегментации трекер работает по
    трёхсекундным чанкам и на границах реплик путает голоса. Это не поломка,
    но и не то, что обещано, — значит человек должен знать.
    """
    if not enabled:
        return ("живая диаризация выключена в конфиге (sufler.live_diarize) — "
                "метки пойдут по каналам")
    if not model_path.exists():
        return ("живой диаризации нет: не найден models/diar/embedding.onnx — "
                "метки пойдут по каналам, где взять модель: docs/DIARIZATION.md")
    if seg_path is not None and not seg_path.exists():
        return ("живая диаризация в упрощённом режиме: нет "
                "models/diar/segmentation.onnx, голоса на границах реплик будут "
                "путаться — поставить: scripts/get_models.py --segmentation")
    return None


class SpeakerTracker:
    def __init__(self, model_path: pathlib.Path, sample_rate: int = 16000,
                 threshold: float = 0.45, min_sec: float = 1.2, max_speakers: int = 8,
                 sticky: float = 0.15):
        import sherpa_onnx
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=1))
        self.sr = sample_rate
        self.threshold = threshold
        self.sticky = sticky            # гистерезис: инерция текущего голоса
        self.min_samples = int(min_sec * sample_rate)
        self.max_speakers = max_speakers
        self._centroids: list[np.ndarray] = []
        self._counts: list[int] = []
        self._last: int | None = None   # последний выданный номер (инерция)
        self._cand: np.ndarray | None = None  # чужой чанк, ждущий подтверждения

    def _embed(self, chunk: np.ndarray) -> np.ndarray | None:
        s = self._ex.create_stream()
        s.accept_waveform(self.sr, chunk)
        s.input_finished()
        if not self._ex.is_ready(s):
            return None
        emb = np.asarray(self._ex.compute(s), dtype=np.float32)
        n = float(np.linalg.norm(emb))
        return emb / n if n > 0 else None

    def _update(self, i: int, emb: np.ndarray):
        k = self._counts[i]  # скользящий центроид: голос «дообучается» по ходу
        c = (self._centroids[i] * k + emb) / (k + 1)
        self._centroids[i] = c / float(np.linalg.norm(c))
        self._counts[i] += 1

    def label(self, chunk: np.ndarray) -> int | None:
        """Номер голоса (1..N); None — только пока ни один голос не установлен.

        Шумные 3с-чанки мигали метками (1↔2) и рвали абзац одного человека
        на куски — теперь: инерция текущего голоса (порог-sticky), смена или
        новый голос только по двум согласным чанкам, короткий кусок =
        продолжение текущего.
        """
        if len(chunk) < self.min_samples:
            return self._last
        emb = self._embed(chunk)
        if emb is None:
            return self._last
        if not self._centroids:  # первый голос встречи не задерживаем
            self._centroids.append(emb)
            self._counts.append(1)
            self._last = 1
            return 1
        sims = [float(np.dot(emb, c)) for c in self._centroids]
        cur = (self._last - 1) if self._last else None
        cur_sim = sims[cur] if cur is not None else -1.0
        best = int(np.argmax(sims))
        # 1) текущий голос уверенно узнан — продолжаем и дообучаем
        if cur_sim >= self.threshold:
            self._update(cur, emb)
            self._cand = None
            return self._last
        # 2) ОТНОСИТЕЛЬНАЯ смена: другой голос заметно ближе текущего. Абсолютные
        #    пороги плывут между звонком (чужие ≤0.16) и очной комнатой через один
        #    микрофон (чужие до ~0.43, свои от ~0.29 — зоны перекрываются); дельта
        #    к текущему от акустики канала не зависит
        if best != cur and sims[best] >= 0.35 and sims[best] - max(cur_sim, 0.0) >= 0.12:
            self._update(best, emb)
            self._last = best + 1
            self._cand = None
            return self._last
        # 3) серая зона продолжения — тянем текущего без дообучения центроида
        if cur_sim >= self.threshold - self.sticky:
            self._cand = None
            return self._last
        # 4) все далеко: новый голос только по двум взаимно согласным чанкам
        #    (сырой-к-сырому у одного голоса ≥~0.45) — шумный одиночный кусок
        #    не плодит фантомов и не рвёт абзац
        if self._cand is not None and float(np.dot(emb, self._cand)) >= 0.45:
            if len(self._centroids) < self.max_speakers:
                c = emb + self._cand
                c /= float(np.linalg.norm(c))
                self._centroids.append(c)
                self._counts.append(2)
                self._last = len(self._centroids)
            self._cand = None
            return self._last
        self._cand = emb
        return self._last

    @property
    def voices(self) -> int:
        return len(self._centroids)


class SegmentTracker:
    """Метка голоса для чанка через куски речи, а не через три секунды целиком.

    Прежний SpeakerTracker считал эмбеддинг со всего чанка. На границе реплик в
    чанк попадает конец фразы одного человека и начало фразы другого, эмбеддинг
    выходит смешанным, косинус ко всем центроидам — средним, и трекер залипает
    на первом голосе: замер на синтетическом диалоге из четырёх голосов дал
    DER 0.725 при ОДНОМ найденном голосе. Порог на это не влияет — от 0.25 до
    0.55 результат одинаковый.

    Здесь та же модель сегментации, что уже работает в проходе после встречи,
    находит внутри чанка куски речи; эмбеддинг считается по куску. На той же
    фикстуре — DER 0.246 и все четыре голоса.

    Метка на чанк остаётся одна: у чанка один текст от STT, поэтому берётся
    говорящий, занявший в нём больше времени. Отдавать метки по кускам можно
    будет, когда по кускам пойдёт и распознавание, — это следующий шаг, и он
    стоит ещё вдвое меньше ошибок (замер: DER 0.090).

    Биометрия не хранится: центроиды живут в объекте, объект — во встрече.
    """

    def __init__(self, seg_model: pathlib.Path, emb_model: pathlib.Path,
                 sample_rate: int = 16000, threshold: float = 0.62,
                 min_segment: float = 0.4, min_new: float = 0.8,
                 max_speakers: int = 8, min_stt: float = 1.0,
                 step_s: float = 2.5):
        import sherpa_onnx

        self.sr = sample_rate
        # Порог 0.62 — из замера: 0.55 склеивает разных людей (DER 0.569),
        # 0.7 плодит лишние голоса. Между 0.6 и 0.65 результат стабилен.
        self.threshold = threshold
        self.min_segment = min_segment
        # Новый голос заводим только по куску подлиннее: на полусекундном
        # «угу» эмбеддинг слишком шумный, чтобы объявлять нового человека.
        self.min_new = min_new
        self.max_speakers = max_speakers
        # Окно отдельного распознавания — от секунды: короче GigaAM теряет
        # края фраз, а стенограмма рассыпается на однословные «микро-метки».
        self.min_stt = min_stt
        # Шаг нарезки чанков (2.5 при чанке 3.0 и перекрытии 0.5): сегмент,
        # живущий целиком в зоне перекрытия и обрезанный правым краем,
        # придерживается — следующий чанк принесёт его полностью.
        self.step_s = step_s
        # Склейка кусков одного НЕЗНАКОМЦА внутри чанка: по замеру сырой-к-
        # сырому у одного голоса ≥~0.45, у разных через один микрофон ≤~0.43.
        self.new_glue = 0.45
        self._diar = sherpa_onnx.OfflineSpeakerDiarization(
            sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(seg_model))),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(emb_model)),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1,
                                                            threshold=0.8),
                min_duration_on=0.3,
                min_duration_off=0.5))
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model)))
        self._centroids: list[np.ndarray] = []
        self._counts: list[int] = []
        self._last_by_channel: dict[str, int | None] = {}

    def _embed(self, piece: np.ndarray) -> np.ndarray | None:
        stream = self._ex.create_stream()
        stream.accept_waveform(self.sr, piece)
        stream.input_finished()
        if not self._ex.is_ready(stream):
            return None
        emb = np.asarray(self._ex.compute(stream), dtype=np.float32)
        n = float(np.linalg.norm(emb))
        return emb / n if n > 0 else None

    def _learn(self, i: int, emb: np.ndarray, weight: float = 1.0) -> None:
        # скользящий центроид: голос «дообучается» по ходу; вес — длительность
        # куска, чтобы полсекундное «угу» не двигало центроид как двухсекундная
        # фраза (ревью 15.08)
        k = self._counts[i]
        c = (self._centroids[i] * k + emb * weight) / (k + weight)
        self._centroids[i] = c / float(np.linalg.norm(c))
        self._counts[i] += weight

    def split(self, chunk: np.ndarray, channel: str = "_default") -> SplitResult:
        """Позиционная раскладка чанка: окна STT по голосам вместо одной метки.

        Назначение голосов идёт по снапшоту центроидов, а дообучение и
        заведение новых — одним махом в конце и только по кускам, реально
        вошедшим в план: придержанный на границе хвост придёт целиком в
        следующем чанке (перекрытие 0.5 с) и не должен учить центроид дважды,
        а падение по дороге не оставляет центроиды полуобновлёнными —
        откат на старый путь остаётся откатом, а не «тем же звуком ещё раз».

        _last — по каналам: короткая пауза в BlackHole не должна получать
        метку последнего говорившего в микрофон (замечание ревью 15.08).
        """
        last = self._last_by_channel.get(channel)
        if chunk is None or len(chunk) < int(self.min_segment * self.sr):
            return SplitResult(None, last)
        try:
            segments = self._diar.process(chunk).sort_by_start_time()
        except Exception:  # noqa: BLE001 — диаризация вспомогательна
            return SplitResult(None, last)

        # 1) эмбеддинги и назначения по снапшоту; новые голоса — пока кандидаты
        entries: list[tuple[float, float, float, np.ndarray, int | None]] = []
        # кластер кандидата: [(emb, seconds, (start_s, end_s)), ...]
        news: list[list[tuple[np.ndarray, float, tuple[float, float]]]] = []
        for seg in segments:
            a = max(0, int(seg.start * self.sr))
            b = min(len(chunk), int(seg.end * self.sr))
            seconds = (b - a) / self.sr
            if seconds < self.min_segment:
                continue
            emb = self._embed(chunk[a:b])
            if emb is None:
                continue
            start_s, end_s = a / self.sr, b / self.sr
            sims = [float(np.dot(emb, c)) for c in self._centroids]
            best = int(np.argmax(sims)) if sims else -1
            voice: int | None = None
            if best >= 0 and sims[best] >= self.threshold:
                voice = best
            elif seconds >= self.min_new:
                # полусекундное «угу» нового человека голос не заводит; куски
                # одного незнакомца внутри чанка слипаются в один кандидат.
                # Лимит голосов здесь НЕ проверяется: кандидат, который потом
                # умрёт без окна, не должен съесть слот у живого (ревью 15.08)
                for k, cluster in enumerate(news):
                    if float(np.dot(emb, cluster[0][0])) >= self.new_glue:
                        cluster.append((emb, seconds, (start_s, end_s)))
                        voice = -(k + 1)
                        break
                else:
                    news.append([(emb, seconds, (start_s, end_s))])
                    voice = -len(news)
            entries.append((start_s, end_s, seconds, emb, voice))

        raw = [(s, e, v) for s, e, _sec, _emb, v in entries]
        windows, deferred, kept = plan_pieces(raw, len(chunk), self.sr,
                                              min_stt=self.min_stt,
                                              step_s=self.step_s)
        kept_keys = {(s, e, v) for s, e, v in kept}
        spans: dict[int, list[tuple[float, float]]] = {}
        for _a, _b, v, rs, re_ in windows:
            spans.setdefault(v, []).append((rs, re_))

        def window_overlap(s: float, e: float, v: int) -> float:
            """Сколько секунд куска дошло до окон СВОЕГО голоса. По
            пересечению, не по вложению: вложенное чужое «угу» режет кусок
            монолога на части, и целиком он не входит ни в одну — вложение
            хоронило живого кандидата вместе с монологом (ревью 15.08 ×4)."""
            return sum(max(0.0, min(e, re_) - max(s, rs))
                       for rs, re_ in spans.get(v, ()))

        def in_window(s: float, e: float, v: int) -> bool:
            return window_overlap(s, e, v) > 1e-6

        # 2) транзакция. Новые голоса: только кластеры, чьи куски дошли до
        # окон; центроид — из этих кусков, взвешенных длительностью
        # (придержанный хвост кандидата не учит — ревью 15.08); при нехватке
        # слотов первыми заводятся те, кто дольше говорил.
        alive: list[tuple[float, int, list[tuple[np.ndarray, float]]]] = []
        for k, cluster in enumerate(news):
            vid = -(k + 1)
            used = [(emb, window_overlap(s, e, vid))
                    for emb, _sec, (s, e) in cluster
                    if in_window(s, e, vid)]
            if used:
                alive.append((sum(sec for _e, sec in used), k, used))
        renum: dict[int, int] = {}
        # вес округляется до миллисекунд: float-шум (2e-16) не должен решать,
        # кто станет «Собеседником 1»; при равных весах — кто заговорил раньше
        for _dur, k, used in sorted(alive,
                                    key=lambda t: (-round(t[0], 3), t[1])):
            if len(self._centroids) >= self.max_speakers:
                break
            c = np.sum([e * s for e, s in used], axis=0)
            c /= float(np.linalg.norm(c))
            self._centroids.append(c)
            self._counts.append(float(sum(s for _e, s in used)))
            renum[-(k + 1)] = len(self._centroids) - 1

        # Существующие голоса: kept-куски дообучают центроид (вес — секунды),
        # но в talk идут только куски из окон — иначе одинокий микро-кусок
        # выбирал бы метку целому чанку в обход min_stt (ревью 15.08).
        talk: dict[int, float] = {}
        for start, end, seconds, emb, voice in entries:
            if voice is None or (start, end, voice) not in kept_keys:
                continue
            idx = renum.get(voice, voice)
            if idx < 0:      # кандидат, чьё окно не выжило: голос не заводим
                continue
            if voice >= 0:   # существующий голос дообучается, новый уже собран
                self._learn(idx, emb, weight=seconds)
            got = window_overlap(start, end, voice)
            if got > 1e-6:
                talk[idx] = talk.get(idx, 0.0) + got

        pieces = [Piece(int(a * self.sr), int(b * self.sr),
                        renum.get(v, v) + 1,
                        int(rs * self.sr), int(re_ * self.sr))
                  for a, b, v, rs, re_ in windows if renum.get(v, v) >= 0]
        # Исключённая НАЗНАЧЕННАЯ речь: придержка, микро-кусок известного или
        # окно кандидата, не получившего слот (ревью 15.08 ×2 — отвергнутый
        # лимитом кандидат тоже чужая речь, а не «ничего»). Такая речь
        # запрещает фолбэк на STT целого чанка и, без окон, требует пропуска.
        assigned_excluded = deferred or any(
            v is not None and ((s, e, v) not in kept_keys
                               or not in_window(s, e, v))
            for s, e, _sec, _emb, v in entries) or any(
            renum.get(v, v) < 0 for _a, _b, v, _rs, _re in windows)
        # Кусок без назначения (короткий незнакомец): при наличии окон он
        # тоже запрещает фолбэк — его слова уехали бы главному; но чанк из
        # одних таких кусков остаётся честным fail-open, а не пропуском.
        unknown_speech = any(v is None for _s, _e, _sec, _emb, v in entries)

        if pieces:
            main = (max(talk, key=lambda k: talk[k]) + 1) if talk else last
            if main is not None:
                self._last_by_channel[channel] = main
            if (len({p.voice for p in pieces}) >= 2 or assigned_excluded
                    or unknown_speech):
                return SplitResult(pieces, main)
            return SplitResult(None, main)  # один голос, всё покрыто: чанк целиком
        if assigned_excluded:
            return SplitResult([], last)    # всё исключено политикой: не распознавать
        return SplitResult(None, last)      # назначений нет вовсе: честный fail-open

    def label(self, chunk: np.ndarray, channel: str = "_default") -> int | None:
        """Номер голоса (1..N) для чанка; None — пока сказать нечего.

        Контракт тот же, что у SpeakerTracker: демон не переписывается.
        """
        return self.split(chunk, channel).main

    @property
    def voices(self) -> int:
        return len(self._centroids)
