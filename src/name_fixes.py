"""Имена меток стенограммы после облачной ревизии (№239).

Пересборка называет дорожки диаризации по обращениям («Саш, нормально?» →
дорожка отвечающего = Саша) и ошибается: 11.09 дорожка ведущего получила
имя «Сергей», а ревизия по тем же обращениям установила Сашу. Ревизия
писала это прозой, и всё, что построено на стенограмме, — заголовки реплик,
участники минуток, копии в архиве и Документации, узел Люди/Сергей со
встречей и связями — оставалось с чужим именем.

Теперь ревизия отдаёт исправления строгим разделом «## Исправления имён»:
«- **Метка** → **Имя** — основание: …». Мост переименовывает заголовки
реплик стенограммы (`**Метка** [чч:мм]:` — ровно такая метка, целиком, только
в речи, не в «Ко-мышлении») и метку в строке участников минуток; версия до
правки уходит в .prev/, хеш машинного текста в сайдкаре обновляется, чтобы
следующая пересборка не приняла правку за ручную. Поручения минуток под
ошибочной меткой ревизия снимает и восстанавливает сама («## Снятые /
Восстановленные поручения»): «**Сергей** — …» могло быть поручением и
настоящему Сергею, упомянутому в речи, — наугад это не переименовывается.
Узлы графа правит облако в режиме правки (промпт); без права правки
воркер пишет в лог, что перенести руками.
"""
from __future__ import annotations

import pathlib
import re

import channel_labels
import live_sidecar
import review_bridge
import safe_write
import transcript

NAMES_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*исправлени[ея] им[её]н\s*(?:\*\*)?\s*[:：.]?\s*(?:\*\*)?\s*$",
                        re.IGNORECASE)
NAMES_WORD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*исправлени\w* им[её]н", re.IGNORECASE | re.MULTILINE)
_FIX = re.compile(r"^\*\*(?P<label>[^*\n]+?)\*\*\s*(?:→|->|=>|—>|⇒)\s*\*\*(?P<name>[^*\n]+?)\*\*"
                  r"\s*(?:[—–-]\s*(?:основание|причина|reason|原因)\s*[:：]\s*(?P<why>.+?))?\s*$",
                  re.IGNORECASE)
_BAD_NAME = re.compile(r"[\[\]*|#\n]")
_PARTICIPANTS = re.compile(r"^(?P<head>\s*(?:.*?\*\*Участники:\*\*|Участники:)\s*)(?P<rest>.*)$", re.M)
MAX_NAME = 60


def name_fixes(review: str, dropped: list[str] | None = None) -> list[tuple[str, str, str]]:
    """(метка, имя, основание) из раздела «## Исправления имён» ревизии.
    Строка не по форме — в `dropped`, не пункт. Раздела нет — пусто."""
    out: list[tuple[str, str, str]] = []
    for item in review_bridge._section_items(review, NAMES_HEAD, dropped):
        m = _FIX.match(item)
        if not m:
            if dropped is not None:
                dropped.append(item)
            continue
        out.append((m.group("label").strip(), m.group("name").strip(), (m.group("why") or "").strip()))
    return out


def section_present(review: str) -> bool:
    """В ревизии есть слова об исправлениях имён — если применить нечего,
    мосту есть что сказать в лог (как у восстановленных/снятых)."""
    return bool(NAMES_WORD.search(review or ""))


def plan(fixes: list[tuple[str, str, str]], headers: set[str], protected: set[str],
         dropped: list[str] | None = None) -> dict[str, str]:
    """Метка → имя, что реально применимо. Не применяется: та же метка;
    метка микрофона владельца (канал — факт железа, не догадка модели);
    имя-заглушка («Собеседник 3»), мусор или слишком длинное; метки нет в
    заголовках реплик; вторая правка той же метки. Причина — в `dropped`.
    Обмен двух меток (A→B, B→A) и слияние в существующую чужую метку —
    применимы: подстановка идёт одним проходом."""
    mapping: dict[str, str] = {}
    for label, name, _why in fixes:
        reason = ""
        if label == name:
            reason = "то же имя"
        elif label in protected:
            reason = "метка владельца (канал микрофона) не переименовывается"
        elif not name or channel_labels.is_neutral_label(name) or _BAD_NAME.search(name) or len(name) > MAX_NAME:
            reason = "имя не годится (заглушка, разметка или длина)"
        elif label not in headers:
            reason = "такой метки в заголовках реплик нет"
        elif label in mapping:
            reason = "метка уже исправлена другой строкой"
        if reason:
            if dropped is not None:
                dropped.append(f"«{label} → {name}» — {reason}")
            continue
        mapping[label] = name
    return mapping


def rename_headers(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Заголовки реплик с меткой из `mapping` — под новым именем; хвост
    «Ко-мышление» не трогается. Возвращает (текст, сколько заголовков)."""
    if not mapping:
        return text, 0
    cut = transcript.notes_start(text)
    speech, tail = text[:cut], text[cut:]
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        spk = m.group("spk").strip()
        if spk not in mapping:
            return m.group(0)
        n += 1
        return "**" + mapping[spk] + "**" + m.group(0)[m.end("spk") + 2 - m.start():]

    return transcript.BLOCK_RE.sub(sub, speech) + tail, n


def rename_participants(minutes: str, mapping: dict[str, str]) -> str:
    """Строка участников минуток («**Участники:** Сергей, Мария» или
    «Участники: …») — метки под новыми именами, целыми словами; остальной
    текст минуток не трогается (поручения — дело ревизии, см. модуль)."""
    if not mapping:
        return minutes
    m = _PARTICIPANTS.search(minutes)
    if not m:
        return minutes
    rest = m.group("rest")
    for label, name in mapping.items():
        rest = re.sub(r"(?<!\w)" + re.escape(label) + r"(?!\w)", lambda _m, n=name: n, rest)
    return minutes[:m.start("rest")] + rest + minutes[m.end("rest"):]


def _machine_owned(live: pathlib.Path, key: str, text: str) -> bool:
    meta = live_sidecar.read(live) or {}
    expected = live_sidecar.valid_sha(meta.get(key))
    return bool(expected) and expected == live_sidecar.sha(text)


def restamp_transcript(live: pathlib.Path, mapping: dict[str, str]) -> int:
    """Заголовки реплик стенограммы под верными именами. Версия до правки —
    в .prev/ (одно поколение, как у пересборки); хеш машинного текста в
    сайдкаре обновляется, только если он совпадал до правки: правленную
    руками стенограмму пересборка и дальше должна считать ручной."""
    text = live.read_text(encoding="utf-8")
    fixed, n = rename_headers(text, mapping)
    if not n:
        return 0
    owned = _machine_owned(live, "transcript_sha256", text)
    prev_dir = live.parent / ".prev"
    prev_dir.mkdir(exist_ok=True)
    safe_write.write_text(prev_dir / live.name, text)
    safe_write.write_text(live, fixed)
    if owned:
        live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(fixed))
    return n


def restamp_minutes(live: pathlib.Path, mapping: dict[str, str]) -> bool:
    """Строка участников минуток рядом со стенограммой; хеш машинных минуток
    обновляется тем же правилом, что у стенограммы. Нет минуток или строки —
    False."""
    mpath = review_bridge.minutes_path(live)
    if not mpath.is_file():
        return False
    text = mpath.read_text(encoding="utf-8")
    fixed = rename_participants(text, mapping)
    if fixed == text:
        return False
    owned = _machine_owned(live, "minutes_sha256", text)
    safe_write.write_text(mpath, fixed)
    if owned:
        live_sidecar.remember(live, "minutes_sha256", live_sidecar.sha(fixed))
    return True


def apply(review: pathlib.Path, live: pathlib.Path, cfg: dict,
          dropped: list[str] | None = None) -> tuple[dict[str, str], int, bool]:
    """Исправления имён из ревизии — в стенограмму и минутки этой встречи.
    Возвращает (применённая карта, заголовков реплик, тронута ли строка
    участников минуток). Нет ревизии, раздела или применимых строк — пусто."""
    try:
        text = review.read_text(encoding="utf-8", errors="replace")
        speech = live.read_text(encoding="utf-8")
    except OSError:
        return {}, 0, False
    fixes = name_fixes(text, dropped=dropped)
    if not fixes:
        return {}, 0, False
    headers = {b["speaker"] for b in transcript.parse_blocks(speech)}
    sufler = cfg.get("sufler") or {}
    owner = str(sufler.get("user_name") or "").strip()
    protected = {channel_labels.mic_label_for(cfg), channel_labels.NEUTRAL_MIC}
    if owner:
        protected |= {owner, owner.split()[0]}
    mapping = plan(fixes, headers, protected, dropped=dropped)
    if not mapping:
        return {}, 0, False
    n = restamp_transcript(live, mapping)
    touched = restamp_minutes(live, mapping)
    return mapping, n, touched
