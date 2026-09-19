"""След события канала в документах встречи (№234).

Хаб отдаёт структурное событие `ChannelEvent` (пропал / вернулся / дыра / не
вернулся до конца) из одной точки; здесь — единственный владелец следа:

- **сайдкар** `.md.live.json`, ключ `channel_events` (JSON-список событий) —
  долговечная запись; пишется в момент события через `live_sidecar.remember`
  (слияние, не дамп), читатели — пересборка, архив, будущие потребители;
- **хвост «Ко-мышления» стенограммы** (`Transcript.note`) — человеческая строка
  с истинными границами; хвост не входит в хеш речи производных
  (`transcript.speech_of`), переживает пересборку (переносится regex-ом) и не
  попадает в тезисы архива (у него свой знак);
- **нить встречи** (`Thread.add_system`) — та же строка на экране, без дедупа.

Свёртки по порогу длительности нет: короткий по отчёту эпизод — это реальная
дыра ≥ порога сторожа (критика DS и GLM входного круга). Свёртка — по счёту
ЭПИЗОДОВ: после `LINES_MAX` эпизодов на канал (пара lost+back — две строки
одного эпизода) в хвост и нить идёт только итог; события в сайдкаре — все.
Итог при остановке (`close`) закрывает картину: интервалы без канала и их
сумма, открытые эпизоды — «до конца записи»; после `close` след закрыт и
события не принимает — гейт у владельца, не только у излучателя (критика GLM
круга 2).

Третий источник событий — запись на телефоне (№200): компаньон везёт рядом с
аудио манифест остановки, импорт на Mac конвертирует его в событие
`KIND_STOPPED` этого же следа (`phone_event`), строка и итог — здесь.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
from collections import Counter
from typing import Callable, NamedTuple

import live_sidecar
import safe_write
import transcript

SIDECAR_KEY = "channel_events"
SUMMARY_MARK = "📋 запись неполная"   # начало строки итога; все, кто ищет её в тексте, берут отсюда
LINES_MAX = 6            # ЭПИЗОДОВ на канал в хвост/нить за запись; дальше — итог
#                          (счётчик по событиям съедал лимит за три флап-цикла —
#                          Minor GLM и DS выходного круга)


class Episode(NamedTuple):
    label: str
    start: float | None
    end: float | None            # None — открыт (канал не вернулся, записи ещё идёт)
    revived: bool                # True — канал вернулся (back/gap); False — закрыт остановкой
PHONE = "phone"          # метка записи компаньона: канал у неё один, событие — остановка файла
NAMES = {"blackhole": "системный звук (собеседники)", "mic": "ваш микрофон", PHONE: "запись на телефоне"}
ABSENT = {"blackhole": "без собеседников", "mic": "без вашего голоса"}

# --- Запись на телефоне (№200) ---------------------------------------------
# Компаньон пишет рядом с аудио манифест `<файл>.json` (`Recorder.StopRecord`):
# kind stop|rotate, reason из закрытого списка, at (ISO 8601), seconds, series,
# finalized_ok. На Mac импорт превращает его в событие следа ЭТОГО модуля и
# ничего не формулирует сам (Critical GLM входного круга): формулировка,
# классификатор строк и участие в итоге — у одного владельца, как у №234.
MANIFEST_SUFFIX = ".json"        # имя манифеста = имя аудио + суффикс (контракт `Inbox.sidecar(for:)`)
KIND_STOPPED = "stopped"         # запись на телефоне закрыта не человеком; эпизодом отсутствия канала
#                                  не является — `episodes_of` его не видит (Critical DS и GLM: `end`
#                                  делал бы каждую импортированную встречу «неполной»). Ротация — тоже
#                                  невольная остановка записи В ЭТОМ ФАЙЛЕ: продолжение обещано телефоном
#                                  в момент закрытия, а состоится ли — он не знает (старт после звонка
#                                  мог не подняться). Поэтому в итог входят обе, а формулировка ротации
#                                  не утверждает будущего (Critical DS выходного круга)
PHONE_REASONS = {                # причина — значение из enum компаньона; текст — здесь, один раз
    "call_no_resume": "микрофон не вернулся после звонка",
    "media_reset": "аудиослужба перезапущена",
    "encode_error": "сбой кодека",
    "stalled": "запись не поднялась после застоя",
    "no_stop": "стоп не зафиксирован — приложение не закрыло файл",
}
PHRASE_CUT = "оборвана"          # терминальный стоп: дальше встреча не записана
PHRASE_SPLIT = "прервана"        # ротация: файл закрыт не человеком, продолжение — если запись возобновилась
PHRASE_CONTINUED = "продолжение, если запись возобновилась, следующим файлом"


def _hm(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "время неизвестно"


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"около {seconds} с"
    return f"{seconds // 60} мин"


# Фразы строк следа — одним списком для писателя (`render`) и классификатора
# (`is_trace_line`): второе независимое знание о форме строки расходилось бы
# молча при третьем канале или переформулировке (Minor DS круга 2 по №317)
PHRASE_NOT_CAPTURED = "не захвачен с начала записи"
PHRASE_LOST = "пропал ("
PHRASE_BACK = "снова пишется с"
PHRASE_GAP = ": пробел в записи"
# грамматика следа целиком: знак + имя канала из NAMES + фраза; без имени
# классификатор ловил строки модели вида «⚠️ подрядчик пропал (…)» и вырезал
# их из промптов (Important DS и Minor GLM круга 3 по №317)
_TRACE_RE = re.compile(
    r"^(?:⚠️|✅|⏹) (?:" + "|".join(re.escape(n) for n in NAMES.values()) + r")"
    r"(?: (?:" + "|".join(re.escape(p) for p in (PHRASE_NOT_CAPTURED, PHRASE_LOST, PHRASE_BACK,
                                                  PHRASE_CUT, PHRASE_SPLIT)) + r")"
    r"|" + re.escape(PHRASE_GAP) + r")")


def render(ev) -> str | None:
    """Человеческая строка события; None — строки нет (закрытие эпизода идёт
    в итог). Формулировка — по каналу и фазе: «пропал» только о канале, который
    жил (Important GLM входного круга)."""
    name = NAMES.get(ev.label, ev.label)
    absent = ABSENT.get(ev.label, "без канала")
    if ev.kind == "lost":
        if not ev.died:
            return f"⚠️ {name} {PHRASE_NOT_CAPTURED} — {absent}: {ev.reason}"
        # момент крика подставлять вместо границы нельзя — он позже на 66–96 с;
        # без кадров граница неизвестна и так и говорится (Minor DS)
        when = _hm(ev.stopped_at) if ev.stopped_at else "время неизвестно"
        return f"⚠️ {name} {PHRASE_LOST}{when}) — дальше запись {absent}"
    if ev.kind == "back":
        return f"✅ {name} {PHRASE_BACK} {_hm(ev.at)}; {absent} было {_dur(ev.silent_s)}"
    if ev.kind == "gap":
        return (f"⚠️ {name}{PHRASE_GAP} {_hm(ev.stopped_at)}–{_hm(ev.at)} "
                f"({_dur(ev.silent_s)}), поток перезапущен")
    return None


def _epoch(value) -> float | None:
    """ISO 8601 манифеста (`2026-09-07T16:02:37Z`) → секунды эпохи; мусор → None
    («время неизвестно» в строке, а не ложный момент)."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def phone_event(manifest) -> dict | None:
    """Событие следа из манифеста компаньона; None — события нет: ручной стоп
    (`user`) — не факт о неполноте, мусор — не факт вовсе. `terminal` различает
    стоп и ротацию для читателей (склейка серии, №260); в итог входят обе.
    Поле `cause` занято словарём причин хаба (`audio.CH_CAUSES`), причина
    телефона — `reason` (Critical DS входного круга)."""
    if not isinstance(manifest, dict):
        return None
    kind, reason = manifest.get("kind"), manifest.get("reason")
    if kind not in ("stop", "rotate") or not isinstance(reason, str) or not reason or reason == "user":
        return None
    ev: dict = {"label": PHONE, "kind": KIND_STOPPED, "at": _epoch(manifest.get("at")),
                "reason": reason, "terminal": kind == "stop"}
    for key in ("seconds", "series", "finalized_ok"):
        if manifest.get(key) is not None:
            ev[key] = manifest[key]
    return ev


def _phone_reason(ev: dict) -> str:
    why = PHONE_REASONS.get(ev.get("reason"), str(ev.get("reason")))
    if ev.get("finalized_ok") is False:
        why += ", файл не финализирован"
    return why


def _phone_text(ev: dict) -> str:
    """Одна формулировка события телефона — для строки события и для итога."""
    when, why = _hm(ev.get("at")), _phone_reason(ev)
    if ev.get("terminal"):
        return f"{NAMES[PHONE]} {PHRASE_CUT} {when} ({why})"
    return f"{NAMES[PHONE]} {PHRASE_SPLIT} {when} ({why}) — {PHRASE_CONTINUED}"


def render_phone(ev: dict) -> str | None:
    """Человеческая строка события телефона (лог импорта, нить); None — не оно.
    В документы событие попадает не этой строкой, а итогом `summary_of`: его
    узнают все читатели хвоста (`note_in_tail`, пересборка, `meeting_source`),
    а строка события — только классификатор (Important DS выходного круга)."""
    if ev.get("kind") != KIND_STOPPED:
        return None
    return f"⏹ {_phone_text(ev)}"


class ChannelTrace:
    """Один владелец следа на запись. `note` — `Transcript.note`, `thread_add` —
    `Thread.add_system(text, at)`; обе подстановки — необязательны."""

    def __init__(self, live: pathlib.Path, note: Callable[[str], None] | None = None,
                 thread_add: Callable[[str, str], bool] | None = None,
                 bare: str | None = None) -> None:
        self.live = live
        self.bare = bare                 # посекундный штамп — сайдкар без угадывания (как у ретитла)
        self._note = note
        self._thread_add = thread_add
        self.events: list[dict] = []
        self._episodes_shown: Counter[str] = Counter()   # эпизодов на канал уже в документах
        self._open_shown: set[str] = set()               # открытые эпизоды, чей lost показан
        self.persist_failed = False        # исход ПОСЛЕДНЕЙ записи в сайдкар (список пишется целиком)
        self._warned_persist = False       # строка stderr об отказе — одна
        self.closed = False                # после close() события не принимаются

    def on_event(self, ev) -> None:
        if self.closed:
            return                         # итог написан — событие в документы не попадёт (критика GLM круга 2)
        self.events.append(ev.as_dict())
        self._persist()
        text = render(ev)
        if text is None:
            return
        # потолок — по эпизодам: `back` показывается, если показан его `lost`;
        # начало нового эпизода (lost/gap) сверх потолка — только сайдкар и итог
        if ev.kind == "back":
            if ev.label not in self._open_shown:
                return
            self._open_shown.discard(ev.label)
        else:
            if self._episodes_shown[ev.label] >= LINES_MAX:
                return
            self._episodes_shown[ev.label] += 1
            if ev.kind == "lost":
                self._open_shown.add(ev.label)
        self._say(text, _hm(ev.at))

    def _persist(self) -> None:
        # ключ пишется целиком на каждое событие: список короткий, а крах между
        # событиями не теряет уже записанного (M3 DS входного круга)
        try:
            ok = live_sidecar.remember(self.live, SIDECAR_KEY,
                                       json.dumps(self.events, ensure_ascii=False), self.bare)
        except Exception:  # noqa: BLE001 — след не роняет запись
            ok = False
        if not ok and not self._warned_persist:
            # отказ долговечной записи — не молча: сайдкар и есть носитель следа
            # для пересборки; итог тоже скажет об этом (Important GLM круга 1).
            # flush — строка важнее всего, когда демона добивает watchdog (GLM круга 2)
            self._warned_persist = True
            try:
                print(f"след канала: сайдкар {self.live.name} не пишется — события только в "
                      "хвосте и нити", file=sys.stderr, flush=True)
            except Exception:  # noqa: BLE001
                pass
        # состояние ФАЙЛА — по последней записи: список пишется целиком, и удавшаяся
        # запись после разового отказа значит, что в сайдкаре всё (Important DS круга 2)
        self.persist_failed = not ok

    def _say(self, text: str, at: str) -> None:
        for fn, args in ((self._note, (text,)), (self._thread_add, (text, at))):
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                pass

    def episodes(self) -> list[Episode]:
        return episodes_of(self.events)

    def summary(self) -> str | None:
        """Одна строка итога по накопленным событиям (см. `summary_of`)."""
        return summary_of(self.events)

    def close(self) -> str | None:
        """Итог при остановке — в хвост и нить, один раз. Зовётся из демона ДО
        спавна пересборки: хвост переносится regex-ом, и строка должна лежать в
        файле к тому моменту (Important GLM входного круга)."""
        text = self.summary()
        if text:
            self._say(text, "")
        self.closed = True
        return text


def _merge_windows(eps: list[Episode]) -> list[Episode]:
    """Слить наложившиеся окна одного канала (порядок событий сохраняется).
    Индекс последнего окна метки — рядом, не поиск по значению (Minor DS круга 2)."""
    out: list[Episode] = []
    last: dict[str, int] = {}
    for ep in eps:
        i = last.get(ep.label)
        prev = out[i] if i is not None else None
        if (prev is not None and ep.start is not None and prev.start is not None
                and prev.end is not None and ep.start <= prev.end):
            out[i] = prev._replace(end=None if ep.end is None else max(prev.end, ep.end),
                                   revived=ep.revived)
            continue
        last[ep.label] = len(out)
        out.append(ep)
    return out


def episodes_of(events: list[dict]) -> list[Episode]:
    """Эпизоды из событий: lost открывает, back/end закрывают, gap — сразу
    закрытый. Смена фазы того же эпизода событием не является (хаб не эмитит).
    Окна одного канала, наложившиеся друг на друга (дыра, отсчитанная от
    одного и того же настоящего кадра несколькими gap), сливаются в одно —
    иначе сумма считала бы одну тишину дважды (Important DS выходного круга)."""
    out: list[Episode] = []
    open_: dict[str, int] = {}
    for e in events:
        label, kind = e.get("label", "?"), e.get("kind")
        if kind == "lost":
            if label in open_:
                continue
            open_[label] = len(out)
            # граница — как в событии; None — «время неизвестно», а не момент
            # крика (Important DS круга 2: рендер уже отказался его подставлять)
            out.append(Episode(label, e.get("stopped_at"), None, False))
        elif kind in ("back", "end"):
            i = open_.pop(label, None)
            if i is None:
                out.append(Episode(label, e.get("stopped_at"), e.get("at"), kind == "back"))
            else:
                out[i] = out[i]._replace(end=e.get("at"), revived=kind == "back")
        elif kind == "gap":
            out.append(Episode(label, e.get("stopped_at"), e.get("at"), True))
        # KIND_STOPPED (телефон, №200) — не эпизод отсутствия канала: файл закрыт,
        # интервала «без канала» у него нет; в итог он входит своей строкой
    return _merge_windows(out)


def summary_of(events: list[dict]) -> str | None:
    """Одна строка итога по списку событий: интервалы без канала и их сумма;
    открытый эпизод — «до конца записи». Нет эпизодов — None. Единственный
    рендер итога: демон на стопе, пересборка из сайдкара (№316) и промпты
    производных (№317) получают одну и ту же строку. Ничего из памяти
    процесса (флаг отказа записи) сюда не входит: документ обязан
    восстанавливаться из сайдкара целиком (Important DS входного круга)."""
    eps = episodes_of(events)
    # невольная остановка записи на телефоне — часть итога, и терминальная, и
    # ротация: запись В ЭТОМ ФАЙЛЕ прервана не человеком, документ по нему — не
    # вся встреча; состоялось ли продолжение, телефон в момент закрытия не знает,
    # и формулировка этого не утверждает (Critical DS выходного круга по №200)
    cuts = [e for e in events if e.get("kind") == KIND_STOPPED]
    if not eps and not cuts:
        return None
    parts = []
    for label in dict.fromkeys(e.label for e in eps):
        mine = [e for e in eps if e.label == label]
        spans, total, unknown = [], 0.0, False
        for ep in mine:
            if ep.start is not None and ep.end is not None:
                total += max(0.0, ep.end - ep.start)
            else:
                unknown = True            # граница неизвестна — сумму не выдумываем
            spans.append(f"{_hm(ep.start)}–{_hm(ep.end) if ep.end is not None else 'до конца записи'}")
        shown = ", ".join(spans[:8]) + (f" и ещё {len(spans) - 8}" if len(spans) > 8 else "")
        total_s = _dur(total) + (" без учёта эпизодов с неизвестной границей" if unknown else "")
        parts.append(f"{ABSENT.get(label, label)} {shown} (эпизодов {len(mine)}, всего {total_s})")
    parts.extend(_phone_text(e) for e in cuts)
    return SUMMARY_MARK + ": " + "; ".join(parts)


def events_of(live: pathlib.Path) -> list[dict]:
    """События канала из сайдкара стенограммы. Писатель `_persist` кладёт
    список JSON-СТРОКОЙ (контракт `remember` — строковые значения), читатель
    обязан это знать — пара писатель/читатель в одном модуле (Important GLM
    входного круга по №316). Мусор, чужой тип, неоднозначный сайдкар → []:
    нет событий — нет оговорки, поведение как до №234. Сайдкар — прямой:
    штамп `bare` знает только писатель до переезда пары (см. `meeting_source.of`)."""
    try:
        meta = live_sidecar.read(live) or {}
    except Exception:  # noqa: BLE001
        return []
    raw = meta.get(SIDECAR_KEY)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list) or not all(isinstance(e, dict) for e in raw):
        return []
    return raw


def recording_note(live: pathlib.Path) -> str | None:
    """Оговорка о неполной записи по сайдкару — та же строка, что демон пишет
    на стопе; None, если событий нет."""
    return summary_of(events_of(live))


def last_event_at(events: list[dict]) -> float | None:
    ats = [e.get("at") for e in events if isinstance(e.get("at"), (int, float))]
    return max(ats) if ats else None


def summary_line(events: list[dict]) -> str | None:
    """Готовая строка итога для хвоста «Ко-мышления» — со штампом последнего
    события, как у строк живого контура. Формат строки целиком здесь:
    пересборка не собирает её по кусочкам из приватных хелперов (Minor DS и
    GLM выходного круга по №316)."""
    note = summary_of(events)
    if not note:
        return None
    at = last_event_at(events)
    return f"{_hm(at)} {note}" if at else note


def tail_with_summary(text: str, events: list[dict]) -> tuple[str, int]:
    """Единственное правило дописывания итога в хвост документа: строка итога по
    событиям сайдкара; уже стоит — `(text, 0)`; нет — дописать через владельца
    формата хвоста (`transcript.append_note`). Пересборка, импорт и сверка пар
    зовут это, а не свои условия по маркеру: три писателя с тремя гейтами
    расходились бы молча (Important DS и GLM круга 2 по №200). Идемпотентность —
    по самой строке: новый итог по большему списку событий ложится ниже, и
    `note_in_tail` берёт последний (контракт читателя)."""
    line = summary_line(events)
    if not line or line in text:
        return text, 0
    return transcript.append_note(text, line), 1


def sync_tail(live: pathlib.Path, *, log=print) -> bool:
    """Свести хвост документа с сайдкаром — с гейтом expect по снимку
    (`safe_write.rewrite_file`): импорт и сверка пар ходят по файлам, которые в
    этот момент может переписывать пересборка из другого процесса под своим
    замком; чтение → запись без гейта откатывало бы её финал (Important DS и
    GLM круга 2). Сайдкар — источник, хвост — производная: не дописалось сейчас —
    допишет пересборка тем же правилом. True — строка дописана."""
    events = events_of(live)
    if not summary_line(events):
        return False
    try:
        return safe_write.rewrite_file(live, lambda text: tail_with_summary(text, events),
                                       "итог записи в хвост") > 0
    except safe_write.LostRace as exc:
        log(f"итог записи в хвост не дописан ({exc}) — сайдкар записан, хвост догонит пересборка")
        return False


def _bare_line(line: str) -> str:
    """Строка хвоста без маркера цитаты и штампа «HH:MM »."""
    body = line.strip()
    if body.startswith("> "):
        body = body[2:].strip()
    if len(body) > 6 and body[2] == ":" and body[:2].isdigit() and body[3:5].isdigit():
        body = body[5:].strip()
    return body


def is_trace_line(line: str) -> bool:
    """Строка хвоста — след записи (событие канала из `render` или итог
    `summary_of`), а не мысль модели. Единственный писатель этих строк — этот
    модуль, поэтому и правило «что считать следом» живёт здесь и строится из
    тех же фраз, что рендер: потребители (извлечение графа, блок ко-мышления
    в разборе) не различают их по префиксам сами."""
    body = _bare_line(line)
    return body.startswith(SUMMARY_MARK) or bool(_TRACE_RE.match(body))
