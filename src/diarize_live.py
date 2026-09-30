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

import sherpa_config
from stt_runtime import CHANNEL_LABEL_ONLY


@dataclasses.dataclass(frozen=True)
class Piece:
    """Кусок чанка для отдельного распознавания.

    start/end — pad-окно для STT (сэмплы), raw_start/raw_end — сырые границы
    речи без запаса: по ним считается высота голоса, чтобы в оценку не попал
    сосед из padding (ревью 15.08). voice — номер голоса (1..N) или None:
    речь есть, а голоса нет — кандидату не хватило места среди max_speakers
    (№571). Номер для STT берётся из заданий jobs_for, не отсюда: там None уже
    решён по каналу.
    """
    start: int
    end: int
    voice: int | None
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
    pieces == [..] — распознавать окна, даже если голос в них один. Окно с
    voice None — речь кандидата без места: трекер говорит, что голос чужой и
    неизвестный, а распознавать ли его, решает jobs_for по каналу (№571).
    """
    pieces: list[Piece] | None
    main: int | None


#: Нижняя граница шага нарезки для трекера, секунды.
MIN_STEP_S = 0.5


def tracker_step_s(chunk_s: float, overlap_s: float) -> float:
    """Шаг нарезки чанков хаба для правила придержки трекера — один на демон и прогон
    по записи (№478 B): от него зависит, какой хвост чанка придерживается."""
    return max(MIN_STEP_S, chunk_s - overlap_s)


def heard_pieces(res: "SplitResult", *, channel_label_neutral: bool) -> list[Piece]:
    """Куски раскладки, которые пойдут в STT: все с голосом и куски без голоса
    (кандидат без места, №571) там, где метка канала никого не называет. Одно
    правило для демона (через jobs_for) и прогона тени по записи — зеркала
    отбора в потребителях расходились бы с демоном молча."""
    return [p for p in res.pieces or ()
            if p.voice is not None or channel_label_neutral]


def jobs_for(res: "SplitResult | None", chunk: np.ndarray, *,
             channel_label_neutral: bool) \
        -> list[tuple[np.ndarray, int, np.ndarray | None]] | None:
    """План распознавания чанка по трёхсостоянному контракту SplitResult.

    Чистая функция — стык «раскладка → демон» дважды ловил дыры на ревью
    15.08, поэтому тестируется без потоков и настоящего STT. None — чанк не
    распознавать вовсе (вся речь исключена политикой). Иначе список заданий
    (кусок для STT, голос, сырой кусок для оценки высоты): голос
    CHANNEL_LABEL_ONLY — метка канала (раскладка упала или молчит — повторный
    вызов трекера учил бы центроиды тем же звуком дважды), положительный —
    номер голоса трекера. Голоса None в заданиях не бывает: демон читает None
    как «спроси трекер ещё раз».

    channel_label_neutral — метка канала никого не называет (канал
    собеседников). Тогда окно без голоса (кандидат без места, №571)
    распознаётся под меткой канала: трекер решает, ЧЬЯ речь, но не то,
    распознавать ли её. На микрофоне метка канала — подпись владельца, и такое
    окно выпадает: подписать владельцем чужую речь в комнате — подмена автора
    (круг 1 по №571).
    """
    if res is None:  # split бросил исключение: канальная метка, не voice_label
        return [(chunk, CHANNEL_LABEL_ONLY, None)]
    if res.pieces is not None and not res.pieces:
        return None
    if res.pieces:
        jobs = [(chunk[p.start:p.end],
                 CHANNEL_LABEL_ONLY if p.voice is None else p.voice,
                 chunk[p.raw_start:p.raw_end])
                for p in heard_pieces(res, channel_label_neutral=channel_label_neutral)]
        return jobs or None
    if res.main is not None:
        return [(chunk, res.main, chunk)]
    return [(chunk, CHANNEL_LABEL_ONLY, None)]


def plan_pieces(raw: list[tuple[float, float, int | None]], chunk_len: int,
                sr: int, *, min_stt: float = 1.0, pad: float = 0.25,
                gap: float = 0.4, edge_eps: float = 0.05,
                step_s: float = 2.5,
                barriers: list[tuple[float, float, int | None]] = ()) -> tuple[
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
    # `barriers` — чужая речь, которую распознаёт другое задание (куски трекера вне
    # сегментов потока, №478 B): pad окна в неё не заходит, иначе участок распознаётся
    # дважды под разными людьми (выходной круг GLM по №478 B, I2).
    barriers = list(raw) + list(barriers)

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


def window_overlap_of(windows: list[tuple[float, float, int, float, float]]):
    """Сколько секунд куска дошло до окон СВОЕГО голоса — функция (начало, конец, голос).
    По пересечению, не по вложению: вложенное чужое «угу» режет кусок монолога на части,
    и целиком он не входит ни в одну — вложение хоронило живого кандидата вместе с
    монологом (ревью 15.08 ×4). Одна на трекер и поток (№478 B)."""
    spans: dict[int, list[tuple[float, float]]] = {}
    for _a, _b, v, rs, re_ in windows:
        spans.setdefault(v, []).append((rs, re_))

    def overlap(s: float, e: float, v: int) -> float:
        return sum(max(0.0, min(e, re_) - max(s, rs)) for rs, re_ in spans.get(v, ()))
    return overlap


def assigned_excluded_of(raw: list[tuple[float, float, int | None]],
                         kept: list[tuple[float, float, int]], deferred: bool, overlap) -> bool:
    """Исключённая НАЗНАЧЕННАЯ речь: придержка, или кусок с голосом не дошёл до окон своего
    голоса (микро-кусок). Такая речь запрещает фолбэк на STT целого чанка и, без окон,
    требует пропуска."""
    kept_keys = set(kept)
    return deferred or any(v is not None and ((s, e, v) not in kept_keys or overlap(s, e, v) <= 1e-6)
                           for s, e, v in raw)


def settle(pieces: list[Piece], *, talk: dict[int, float], last: int | None,
           assigned_excluded: bool, unknown_speech: bool) -> SplitResult:
    """Итог раскладки по окнам — трёхсостоянный контракт `SplitResult`, одно правило для
    трекера (`SegmentTracker.split`) и потока Nemotron (`stream_split`, №478 B): две копии
    одного контракта расходились бы молча.

    talk — секунды речи в окнах по номеру голоса: главный голос чанка — больше всех
    говоривший (при равенстве — раньше попавший в словарь), без окон — `last`. Чанк
    целиком (`pieces is None`) — только когда голос один, ничего не исключено и нет ни
    куска без голоса, ни речи без назначения: иначе их слова ушли бы главному."""
    voiceless = any(p.voice is None for p in pieces)
    if pieces:
        main = max(talk, key=lambda k: talk[k]) if talk else last
        if (len({p.voice for p in pieces}) >= 2 or assigned_excluded
                or unknown_speech or voiceless):
            return SplitResult(pieces, main)
        return SplitResult(None, main)  # один голос, всё покрыто: чанк целиком
    if assigned_excluded:
        return SplitResult([], last)    # всё исключено политикой: не распознавать
    return SplitResult(None, last)      # назначений нет вовсе: честный fail-open


def stream_split(raw: list[tuple[float, float, int]], chunk_len: int, sr: int, *,
                 step_s: float, min_stt: float = 1.0, unknown_speech: bool = False,
                 barriers: list[tuple[float, float, int | None]] = ()) -> SplitResult:
    """Раскладка чанка по сегментам потока Nemotron (№478 B): те же окна (`plan_pieces`), тот
    же учёт окон и тот же итог (`settle`), что у трекера, — меняется только источник голоса.

    raw — (start_s, end_s, метка) в секундах от начала чанка; у каждого сегмента потока
    метка есть. `unknown_speech` — речь, которой поток метки не дал (её слышит трекер):
    она запрещает распознавать чанк целиком под главной меткой. Порядок — по началу, как у
    сегментов трекера: от него зависит, кто главный при равенстве секунд."""
    raw = sorted(raw, key=lambda r: r[0])
    windows, deferred, kept = plan_pieces(raw, chunk_len, sr, min_stt=min_stt, step_s=step_s,
                                          barriers=barriers)
    overlap = window_overlap_of(windows)
    kept_keys = set(kept)
    talk: dict[int, float] = {}
    for s, e, v in raw:
        if (s, e, v) not in kept_keys:
            continue
        got = overlap(s, e, v)
        if got > 1e-6:
            talk[v] = talk.get(v, 0.0) + got
    pieces = [Piece(int(a * sr), int(b * sr), v, int(rs * sr), int(re_ * sr))
              for a, b, v, rs, re_ in windows]
    return settle(pieces, talk=talk, last=None,
                  assigned_excluded=assigned_excluded_of(raw, kept, deferred, overlap),
                  unknown_speech=unknown_speech)


#: Номера меток потока среди номеров голосов демона: выше любого номера трекера
#: (1..max_speakers), чтобы имя голоса (`voice_names` демона) не спутало одно с другим.
STREAM_VOICE_BASE = 1000

#: Задание распознавания: (кусок для STT, номер подписи, сырой кусок для высоты голоса,
#: доли сверки каналов). Номер подписи называет голос в стенограмме (метка потока или голос
#: трекера); доли сверки — голоса трекера, звучавшие в куске, с долей звука куска, от
#: большей к меньшей: на них держится эхо-фильтр `owner_voice.Heard` (номер микрофона даёт
#: только трекер), и каждый голос получает свои секунды, а не один победитель все (выходной
#: круг 1 №478 B, I3). Пусто — сверку этот кусок не кормит.
Share = tuple[int, float]
Job = tuple[np.ndarray, "int | None", "np.ndarray | None", tuple[Share, ...]]


def own_share(n: int | None) -> tuple[Share, ...]:
    """Доли сверки куска трекера: его голос целиком; метка канала — ничего."""
    return ((n, 1.0),) if n is not None and n >= 0 else ()


def with_recon(jobs: list[tuple[np.ndarray, int | None, np.ndarray | None]] | None) -> list[Job] | None:
    """Задания трекера (или канала) в форме `Job`: подпись и сверка — один голос, как до
    потока."""
    if jobs is None:
        return None
    return [(piece, n, raw, own_share(n)) for piece, n, raw in jobs]


def tracker_speech(res: SplitResult | None, chunk_len: int, *,
                   neutral: bool) -> list[tuple[int, int, int | None]]:
    """Речь чанка, которую распознал бы трекер (режим `off`), — (начало, конец, голос|None)
    в сэмплах от начала чанка, по тому же правилу, что `jobs_for`, для всех трёх состояний
    `SplitResult`: раскладка упала — весь чанк без голоса; чанк целиком — весь чанк под
    `main` (или без голоса); `[]` — ничего (всё снято политикой: придержка, микро-куски);
    окна — их сырые границы, куски без голоса — только там, где метка канала никого не
    называет (`heard_pieces`, №571). Одна функция на всех потребителей: покрытие речи и
    доли сверки не выводятся из остатков раскладки каждый по-своему (финальный Opus по
    №478 B, C1)."""
    if res is None:
        return [(0, chunk_len, None)]
    if res.pieces is None:
        return [(0, chunk_len, res.main)]
    return [(p.raw_start, p.raw_end, p.voice) for p in heard_pieces(res, channel_label_neutral=neutral)]


def tracker_spans(res: SplitResult | None, chunk_len: int) -> list[tuple[int, int, int]]:
    """Где в чанке какой голос трекера — для долей сверки: речь трекера с голосом."""
    return [(s, e, v) for s, e, v in tracker_speech(res, chunk_len, neutral=True) if v is not None]


def uncovered(speech: list[tuple[int, int, int | None]], covered: list[tuple[int, int]],
              min_len: int) -> tuple[list[tuple[int, int, int | None]], int]:
    """Речь вне покрытия: части отрезков `speech` вне объединения `covered` — (части не
    короче `min_len`, сэмплы в частях короче). Короткий остаток распознавать нечем (окно STT
    от секунды, а отрезок меньше порога — край чужого окна), он идёт в счёт `lost_s`."""
    cover = sorted(covered)
    parts: list[tuple[int, int, int | None]] = []
    lost = 0
    for start, end, voice in speech:
        pos = start
        for s, e in cover + [(end, end)]:
            if e <= pos:
                continue
            gap_end = min(max(s, pos), end)
            if gap_end > pos:
                if gap_end - pos >= min_len:
                    parts.append((pos, gap_end, voice))
                else:
                    lost += gap_end - pos
            pos = max(pos, min(e, end))
            if pos >= end:
                break
    return parts, lost


#: Доля звука куска, с которой голос трекера входит в сверку: касание в несколько сэмплов
#: — не голос этого куска, в счёт голосов сайдкара он не идёт (выходной круг 2 №478 B, M1).
MIN_SHARE = 0.1


def recon_shares(spans: list[tuple[int, int, int]], start: int, end: int) -> tuple[Share, ...]:
    """Доли сверки куска [start, end): голоса трекера, пересёкшиеся с ним, и доля звука куска
    у каждого — от большей к меньшей (при равенстве — в порядке раскладки). Касание —
    не пересечение."""
    got: dict[int, int] = {}
    for s, e, v in spans:
        overlap = min(e, end) - max(s, start)
        if overlap > 0:
            got[v] = got.get(v, 0) + overlap
    length = max(1, end - start)
    return tuple((v, min(1.0, got[v] / length))
                 for v in sorted(got, key=lambda v: -got[v]) if got[v] / length >= MIN_SHARE)


#: Речь трекера вне окон потока короче этого (секунды) — не отдельное задание, а `lost_s`:
#: край соседнего окна, распознавать нечем. Порог — минимальный сегмент трекера.
UNLABELLED_S = 0.4


def stream_layout(raw: list[tuple[float, float, int]], speech: list[tuple[int, int, int | None]],
                  chunk_len: int, sr: int, *, step_s: float, min_stt: float = 1.0,
                  bounded: bool = True) -> tuple[SplitResult, list[tuple[int, int, int | None]], int]:
    """Раскладка чанка режима `on` с сохранением речи (финальный Opus по №478 B, C1): поток
    говорит, КТО, но не решает, ЧТО распознавать, — каждый сэмпл речи трекера (`speech`,
    то, что распознал бы `off`) лежит ровно в одном задании.

    1. Окна потока (`plan_pieces`) и их сырые границы — покрытие: микросегмент без окна и
       придержанный хвост не покрывают ничего.
    2. Речь трекера вне покрытия, не короче `UNLABELLED_S`, — отдельными заданиями трекера;
       короче — счёт потерянных сэмплов (`lost_s` строки журнала).
    3. Если такие задания есть, окна потока строятся заново с ними как барьерами: запас окна
       не заходит в звук, который распознаёт трекер, и чанк не идёт целиком под одной
       меткой. Сырые границы окон при этом не меняются: барьер лежит вне них.

    `bounded` — у речи трекера есть границы (окна). Чанк целиком и упавшая раскладка границ
    не несут, только запрет потерять чанк: есть окна потока — они и решают, что распознавать,
    нет ни одного — чанк целиком уходит заданием трекера. Иначе тишина и хвост чанка шли бы
    отдельными заданиями короче секунды почти на каждом чанке (круг проверки правок Opus,
    Sonnet I1, GLM M2).

    Возвращает (раскладка потока, задания трекера (начало, конец, голос), потерянные сэмплы)."""
    raw = sorted(raw, key=lambda r: r[0])
    windows, _deferred, _kept = plan_pieces(raw, chunk_len, sr, min_stt=min_stt, step_s=step_s)
    covered = [(int(rs * sr), int(re_ * sr)) for _a, _b, _v, rs, re_ in windows]
    min_len = round(UNLABELLED_S * sr)
    if not bounded and covered:
        speech = []                            # границ нет — окна потока решают сами
    extra, _short = uncovered(speech, covered, min_len)
    res = stream_split(raw, chunk_len, sr, step_s=step_s, min_stt=min_stt,
                       unknown_speech=bool(extra),
                       barriers=[(a / sr, b / sr, None) for a, b, _v in extra])
    # потери — всё, что из речи трекера не попало в звук STT на самом деле (окна с запасом,
    # чанк целиком, задания трекера), любой длины: и край короче порога, и то, что не
    # должно было остаться (круг проверки правок Opus, Sonnet I2)
    final = ([] if res.pieces == [] else [(0, chunk_len)] if res.pieces is None
             else [(p.start, p.end) for p in res.pieces])
    rest, _none = uncovered(speech, final + [(a, b) for a, b, _v in extra], 1)
    return res, extra, sum(e - s for s, e, _v in rest)


class StreamVoices:
    """Метки потока Nemotron одной встречи (№478 B, режим `on`) — живут в нити STT.

    Метка — `STREAM_VOICE_BASE + поколение`, а не номер слота: движок отдаёт слот другому
    человеку, когда прежний замолчал, и имя, данное слоту (`name_loop`), уверенно подписало
    бы чужую речь. Слот, молчавший дольше `gap_s`, получает новую метку: лишнее дробление
    дешевле чужого имени, итог всё равно даёт пересборка. Молчанием считается и время, пока
    куски шли мимо потока (фолбэк): реестр видит только разложенные потоком чанки.

    Таблица «голос трекера → последняя метка потока»: кусок, разложенный трекером (поток не
    успел, умер или молчит), берёт метку из неё — иначе тот же человек на соседнем куске
    назывался бы вторым именем; связи нет — номер трекера. Заполняет её тот же расчёт, что
    даёт номер сверки."""

    def __init__(self, *, sr: int, gap_s: float):
        self._sr = sr
        self._gap = round(gap_s * sr)
        self._slots: dict[int, list[int]] = {}      # слот → [метка, конец речи на оси хаба]
        self._generation = 0
        self._link: dict[int, int] = {}             # голос трекера → последняя метка потока

    def _label(self, slot: int, start: int, end: int) -> int:
        cur = self._slots.get(slot)
        if cur is None or start - cur[1] > self._gap:
            cur = self._slots[slot] = [STREAM_VOICE_BASE + self._generation, end]
            self._generation += 1
        cur[1] = max(cur[1], end)
        return cur[0]

    def fallback(self, jobs: list[tuple[np.ndarray, int | None, np.ndarray | None]] | None) \
            -> list[Job] | None:
        """Задания трекера, подписанные через таблицу связей; номер сверки — голос трекера."""
        if jobs is None:
            return None
        return [(piece, self._link.get(n, n) if n is not None and n >= 0 else n, raw, own_share(n))
                for piece, n, raw in jobs]

    def plan(self, segs: list[tuple[int, int, int]] | None, *, origin: int, chunk: np.ndarray,
             tracker: SplitResult | None,
             tracker_jobs: list[tuple[np.ndarray, int | None, np.ndarray | None]] | None,
             neutral: bool, step_s: float, min_stt: float = 1.0) -> tuple[list[Job] | None, dict]:
        """Задания чанка и поля строки журнала. segs — сегменты потока (начало, конец, слот)
        на оси хаба, задевающие чанк с началом `origin`; None — метки нет (решила тень).
        Пусто — поток в чанке речи не слышит: подписывать нечем, куски — трекеру."""
        if segs is None:
            return self.fallback(tracker_jobs), {"source": "tracker"}
        if not segs:
            return self.fallback(tracker_jobs), {"source": "tracker", "fallback": "no_speech"}
        n = len(chunk)
        raw = [((max(s, origin) - origin) / self._sr, (min(e, origin + n) - origin) / self._sr,
                self._label(slot, max(s, origin), min(e, origin + n)))
               for s, e, slot in sorted(segs) if min(e, origin + n) > max(s, origin)]
        if not raw:                                  # сегменты лишь касаются чанка — речи в нём нет
            return self.fallback(tracker_jobs), {"source": "tracker", "fallback": "no_speech"}
        speech = tracker_speech(tracker, n, neutral=neutral)
        res, extra_spans, lost = stream_layout(raw, speech, n, self._sr, step_s=step_s, min_stt=min_stt,
                                               bounded=tracker is not None and tracker.pieces is not None)
        jobs = jobs_for(res, chunk, channel_label_neutral=neutral) or []
        bounds = ([(p.raw_start, p.raw_end) for p in heard_pieces(res, channel_label_neutral=neutral)]
                  if res.pieces else [(0, n)])
        spans = tracker_spans(tracker, n)
        placed: list[tuple[int, Job]] = []
        no_recon = agree = 0
        for (piece, label, raw_piece), (a, b) in zip(jobs, bounds):
            shares = recon_shares(spans, a, b)
            if not shares:
                no_recon += 1
            elif label is not None and label >= STREAM_VOICE_BASE:
                lead = shares[0][0]
                agree += self._link.get(lead) == label
                self._link[lead] = label
            placed.append((a, (piece, label, raw_piece, shares)))
        # речь мимо окон потока — трекеру, под меткой связи его голоса; без голоса — меткой
        # канала (в `speech` она есть только там, где никого не называет, №571)
        for a, b, voice in extra_spans:
            label = CHANNEL_LABEL_ONLY if voice is None else self._link.get(voice, voice)
            placed.append((a, (chunk[a:b], label, chunk[a:b], own_share(voice))))
        fields = {"source": "stream", "pieces": len(placed) - len(extra_spans), "no_recon": no_recon,
                  "recon_agree": agree, "lost_s": round(lost / self._sr, 3)}
        if extra_spans:
            fields["tracker_pieces"] = len(extra_spans)
        if not placed:
            return None, fields
        return [job for _a, job in sorted(placed, key=lambda t: t[0])], fields


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
            sherpa_config.embedding_config(model_path, kind=sherpa_config.LIVE))
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
                segmentation=sherpa_config.segmentation_config(seg_model, kind=sherpa_config.LIVE),
                embedding=sherpa_config.embedding_config(emb_model, kind=sherpa_config.LIVE),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1,
                                                            threshold=0.8),
                min_duration_on=0.3,
                min_duration_off=0.5))
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_config.embedding_config(emb_model, kind=sherpa_config.LIVE))
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
        window_overlap = window_overlap_of(windows)

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
        talk: dict[int, float] = {}          # номер голоса (1..N) → секунды в окнах
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
                talk[idx + 1] = talk.get(idx + 1, 0.0) + got

        # Окно кандидата, не получившего слот, остаётся куском без голоса
        # (voice None), а не выбрасывается: речь чужая, но это речь (№571 —
        # на встрече из восьми голосов так терялось 85 % слов собеседников).
        # Номер собирается мимо «+1»: -1 + 1 = 0, а 0 — валидный голос.
        pieces = [Piece(int(a * self.sr), int(b * self.sr),
                        renum.get(v, v) + 1 if renum.get(v, v) >= 0 else None,
                        int(rs * self.sr), int(re_ * self.sr))
                  for a, b, v, rs, re_ in windows]
        assigned_excluded = assigned_excluded_of(raw, kept, deferred, window_overlap)
        # Кусок без назначения (короткий незнакомец): при наличии окон он
        # тоже запрещает фолбэк — его слова уехали бы главному; но чанк из
        # одних таких кусков остаётся честным fail-open, а не пропуском.
        unknown_speech = any(v is None for _s, _e, _sec, _emb, v in entries)
        res = settle(pieces, talk=talk, last=last, assigned_excluded=assigned_excluded,
                     unknown_speech=unknown_speech)
        if pieces and res.main is not None:
            self._last_by_channel[channel] = res.main
        return res

    def label(self, chunk: np.ndarray, channel: str = "_default") -> int | None:
        """Номер голоса (1..N) для чанка; None — пока сказать нечего.

        Контракт тот же, что у SpeakerTracker: демон не переписывается.
        """
        return self.split(chunk, channel).main

    @property
    def voices(self) -> int:
        return len(self._centroids)
