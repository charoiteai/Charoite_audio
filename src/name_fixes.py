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
# основание — через тире или в скобках: «— основание: …» и «(основание: …)»,
# модель пишет обе формы (DS r1 M6 по #548); скобка без слова «основание» —
# не форма, строка уходит в отброшенные, а не теряет хвост молча (DS r2 M1)
_FIX = re.compile(r"^\*\*(?P<label>[^*\n]+?)\*\*\s*(?:→|->|=>|—>|⇒)\s*\*\*(?P<name>[^*\n]+?)\*\*"
                  r"\s*(?:[—–-]\s*(?:основание|причина|reason|原因)\s*[:：]\s*(?P<why>.+?)"
                  r"|\(\s*(?:основание|причина|reason|原因)\s*[:：]\s*(?P<why2>[^)\n]+?)\s*\))?\s*$",
                  re.IGNORECASE)
_BAD_NAME = re.compile(r"[\[\]*|#\n]")
# строка участников: «**Участники:** …» в шапке минуток (посреди строки) и
# «Участники (звучали в разговоре): …» в шапке стенограммы; хвост головы —
# только пробелы той же строки, не перевод строки (GLM r1 M4 по #548)
_PARTICIPANTS = re.compile(r"^(?P<head>[^\n]*?(?<!\w)(?:\*\*)?Участники[^:\n]*:(?:\*\*)?[ \t]*)(?P<rest>[^\n]*)$", re.M)
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
        out.append((m.group("label").strip(), m.group("name").strip(),
                    (m.group("why") or m.group("why2") or "").strip()))
    return out


def section_present(review: str) -> bool:
    """В ревизии есть слова об исправлениях имён — если применить нечего,
    мосту есть что сказать в лог (как у восстановленных/снятых)."""
    return bool(NAMES_WORD.search(review or ""))


def plan(fixes: list[tuple[str, str, str]], headers: set[str], protected: set[str],
         dropped: list[str] | None = None) -> dict[str, str]:
    """Метка → имя, что реально применимо. Не применяется: та же или пустая
    метка; метка микрофона владельца (канал — факт железа, не догадка
    модели) — ни как метка, ни как цель (иначе чужая дорожка стала бы
    владельцем, DS r1 I4 / GLM r1 M5 по #548); имя-заглушка («Собеседник
    3»), мусор или слишком длинное; метки нет в заголовках реплик; вторая
    правка той же метки; имя — метка ДРУГОЙ живой дорожки, которую никто не
    переименовывает (слияние двух голосов в одного человека без отката —
    не делаем, критика DS r2; обмен A↔B при этом применим: обе дорожки
    переименованы одним проходом). Причина — в `dropped`."""
    mapping: dict[str, str] = {}
    labels = {label for label, _, _ in fixes}
    for label, name, _why in fixes:
        reason = ""
        if label == name:
            reason = "то же имя"
        elif not label:
            reason = "пустая метка"
        elif label in protected:
            reason = "метка владельца (канал микрофона) не переименовывается"
        elif name in protected:
            reason = "целевое имя — метка владельца (канал микрофона)"
        elif not name or channel_labels.is_neutral_label(name) or _BAD_NAME.search(name) or len(name) > MAX_NAME:
            reason = "имя не годится (заглушка, разметка или длина)"
        elif label not in headers:
            reason = "такой метки в заголовках реплик нет"
        elif label in mapping:
            reason = "метка уже исправлена другой строкой"
        elif name in headers and name not in labels:
            reason = f"имя — метка другой дорожки «{name}», слияние дорожек не делаем"
        if reason:
            if dropped is not None:
                dropped.append(f"«{label} → {name}» — {reason}")
            continue
        mapping[label] = name
    # обмен/цепочка обещаны только если ВСЕ участники переименованы: «Б → Я»
    # отклонён, а «А → Б» применился бы слиянием (DS r2 I1, GLM r2 M1)
    for label in [k for k, v in mapping.items() if v in headers and v not in mapping]:
        if dropped is not None:
            dropped.append(f"«{label} → {mapping[label]}» — имя — метка другой дорожки «{mapping[label]}», её правка отклонена")
        del mapping[label]
    return mapping


def _word_map(text: str, mapping: dict[str, str]) -> str:
    """Замена меток целыми словами ОДНИМ проходом: обмен A↔B и цепочка
    A→B→C последовательными заменами схлопывали участников в одно имя
    (GLM r1 Critical, DS r1 I1 по #548); дефис — часть слова, «Анна-Мария»
    не «Мария-Мария» (DS r1 M5)."""
    if not mapping:
        return text
    pat = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
                     + r")(?![\w-])")
    return pat.sub(lambda m: mapping[m.group(0)], text)


def rename_headers(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Заголовки реплик с меткой из `mapping` — под новым именем, и шапка
    «Участники (звучали в разговоре): …» тоже: по ней `participants_of`
    судит «не участник», и старое имя в шапке пропускало бы поручение
    настоящему тёзке (GLM r1 I2 по #548). Хвост «Ко-мышление» не трогается.
    Возвращает (текст, сколько заголовков реплик)."""
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

    return rename_participants(transcript.BLOCK_RE.sub(sub, speech), mapping) + tail, n


def rename_participants(text: str, mapping: dict[str, str]) -> str:
    """Строка участников («**Участники:** Сергей, Мария» в минутках,
    «Участники (звучали в разговоре): …» в стенограмме) — метки под новыми
    именами, целыми словами и одним проходом; остальной текст не трогается
    (поручения — дело ревизии, см. модуль)."""
    if not mapping:
        return text
    m = _PARTICIPANTS.search(text)
    if not m:
        return text
    return text[:m.start("rest")] + _word_map(m.group("rest"), mapping) + text[m.end("rest"):]


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
    prev_dir = live.parent / ".prev"          # версия до правки — как у пересборки (DS r1 I3)
    prev_dir.mkdir(exist_ok=True)
    safe_write.write_text(prev_dir / mpath.name, text)
    safe_write.write_text(mpath, fixed)
    if owned:
        live_sidecar.remember(live, "minutes_sha256", live_sidecar.sha(fixed))
    return True


def planned(review: pathlib.Path, live: pathlib.Path, cfg: dict,
            dropped: list[str] | None = None) -> dict[str, str]:
    """Карта «метка → имя», которую ревизия просит применить, без правки
    файлов. Нужна и без права правки графа: мост поручений считает
    участников по НЕпереименованной стенограмме, и восстановленный пункт с
    верным именем получал бы «⚠ не участник» (DS r2 I2 по #548) — верные
    имена из этой карты мост добавляет к участникам. Файл не в UTF-8 —
    пусто со строкой в `dropped`."""
    try:
        text = review.read_text(encoding="utf-8", errors="replace")
        speech = live.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        if dropped is not None:
            dropped.append(f"{live.name} не в UTF-8 — имена не перештампованы ({e.reason})")
        return {}
    except OSError:
        return {}
    fixes = name_fixes(text, dropped=dropped)
    if not fixes:
        return {}
    headers = {b["speaker"] for b in transcript.parse_blocks(speech)}
    sufler = cfg.get("sufler") or {}
    owner = str(sufler.get("user_name") or "").strip()
    protected = {channel_labels.mic_label_for(cfg), channel_labels.NEUTRAL_MIC}
    if owner:
        protected |= {owner, owner.split()[0]}
    return plan(fixes, headers, protected, dropped=dropped)


def apply(review: pathlib.Path, live: pathlib.Path, cfg: dict,
          dropped: list[str] | None = None) -> tuple[dict[str, str], int, bool]:
    """Исправления имён из ревизии — в стенограмму и минутки этой встречи.
    Возвращает (применённая карта, заголовков реплик, тронута ли строка
    участников минуток). Нет ревизии, раздела или применимых строк — пусто;
    файл не в UTF-8 (правили в чужом редакторе) — пусто со строкой в
    `dropped`, а не исключение: мост поручений должен идти своим ходом
    (DS r1 I2 по #548). Файлы независимы: битые или не записавшиеся минутки
    не отменяют уже перештампованную стенограмму — строка в `dropped`, а
    результат по факту (критика GLM r2, DS r2 M3)."""
    mapping = planned(review, live, cfg, dropped=dropped)
    if not mapping:
        return {}, 0, False
    n = restamp_transcript(live, mapping)
    mpath = review_bridge.minutes_path(live)
    touched = False
    try:
        touched = restamp_minutes(live, mapping)
    except UnicodeDecodeError as e:
        if dropped is not None:
            dropped.append(f"{mpath.name} не в UTF-8 — участники минуток не перештампованы ({e.reason})")
    except OSError as e:
        if dropped is not None:
            dropped.append(f"{mpath.name}: участники минуток не перештампованы ({e})")
    return mapping, n, touched
