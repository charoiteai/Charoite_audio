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
воркер пишет в лог, что перенести руками. Отказ plan() граф не откатывает:
когда раздел применяла машина (правка графа включена и перенос сверен),
строки отказа пишутся в файл ревизии разделом «## Не применено», а итоговая
строка журнала называет их число. Голое имя владельца, его полное имя на
чужой дорожке и метка микрофона — три разные причины.
"""
from __future__ import annotations

import pathlib
import re
import typing

import channel_labels
import live_sidecar
import review_bridge
from charoite_graph import safe_write
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
# «�» — отдельная причина, не «разметка». Имя с этим символом отвергается
# ВСЕГДА, каким бы целым ни был файл ревизии: автором такой символ в имени не
# бывает, он приезжает из испорченного источника, который модель процитировала
# (№263, DS r3 Critical 2). Написать его в заголовки реплик, участников минуток
# и узел Люди значило бы оставить порчу в графе навсегда. Своя строка в логе,
# чтобы человек не искал байты там, где имя отвергли за длину или разметку.
_MANGLED_NAME = "�"
# строка участников: «**Участники:** …» в шапке минуток (посреди строки) и
# «Участники (звучали в разговоре): …» в шапке стенограммы; хвост головы —
# только пробелы той же строки, не перевод строки (GLM r1 M4 по #548)
_PARTICIPANTS = re.compile(r"^(?P<head>[^\n]*?(?<!\w)(?:\*\*)?Участники[^:\n]*:(?:\*\*)?[ \t]*)(?P<rest>[^\n]*)$", re.M)
MAX_NAME = 60

# Три разные причины отказа по имени. Метка микрофона — прежние формулировки.
# Голое имя — первое слово полного имени владельца, и только оно: тёзка,
# записанный с фамилией, этим отказом не цепляется.
REASON_MIC_LABEL = "метка владельца (канал микрофона) не переименовывается"
REASON_MIC_TARGET = "целевое имя — метка владельца (канал микрофона)"
REASON_BARE = "голое имя владельца: напишите имя с фамилией"
REASON_OWNER = "целевое имя — полное имя владельца"
REASON_OWNER_LABEL = "полное имя владельца не переименовывается"
REASON_BARE_LABEL = "голое имя владельца не переименовывается"

# Заголовок ровно такой: слова «исправления имён» ловит NAMES_WORD, а жирная
# форма «**метка** → **имя**» — это _FIX, и раздел снова разобрался бы как правки.
UNAPPLIED_TITLE = "## Не применено"
_UNAPPLIED_HEAD = re.compile(r"^\s*## Не применено\s*$")
_ANY_HEAD = re.compile(r"^#{1,6} ")
# plan() кладёт отказ так: «метка → имя» — причина. Причина сама может
# содержать « — » (каскад слияния), поэтому режется только первый разделитель
# после закрывающей скобки.
_REFUSAL = re.compile(r"^«(?P<label>.*?) → (?P<name>.*?)» — (?P<why>.+)$")

#: Пример обмена двух меток для модели — строгая форма раздела. Метки условные:
#: скопированный на встречу без таких дорожек, пример отсеет plan() («такой
#: метки в заголовках реплик нет»).
SWAP_EXAMPLE = ("- **А** → **Б** — основание: на обращения к Б отвечает дорожка «А»\n"
                "- **Б** → **А** — основание: на обращения к А отвечает дорожка «Б»")

#: Абзац задания ревизии про «## Исправления имён» — один на оба режима.
#: Живёт здесь, рядом с plan(): модель должна знать, что строки применяются
#: одним проходом, иначе найденный обмен двух дорожек она не пишет из страха
#: слить их (№560, встреча 29.09). Кто применит раздел, в момент промпта
#: неизвестно: «перенос сверен» есть только после прогона, и то же обещание
#: ложно в режиме правки без сверки. Правила ниже — отказы plan(), которые
#: модель может нарушить честно.
PROMPT_PARAGRAPH = (
    "Если дорожка стенограммы названа не тем человеком (обращения и ответы на "
    "них, роль, самоназвание показывают другого участника), вынеси это под "
    "заголовком СТРОГО «## Исправления имён»: каждая строка — «- **Метка** → "
    "**Имя** — основание: …», метка — ровно как в заголовках реплик "
    "стенограммы, имя — участник встречи. Строки раздела применяются "
    "одновременно, одним проходом, а не по очереди: две дорожки, перепутанные "
    "местами, — это две строки обмена, пиши обе; так же цепочка (А → Б и Б → В, "
    "где В — новое имя). Пример формы для обмена (метки условные):\n"
    + SWAP_EXAMPLE + "\n"
    "Строка, чьё имя — метка другой дорожки, применится, только если строка этой "
    "дорожки тоже пройдёт. Одна строка на метку. Не применится имя, совпадающее "
    "с меткой другой дорожки, которую ты не переименовываешь (два голоса слились "
    "бы в одного человека), — такое опиши прозой; метку владельца (его микрофон) "
    "не переименовывай и его имя другой дорожке не давай. Если фамилия "
    "участника звучала, пиши ему имя с фамилией — голое имя не применяется. "
    "Раздел применит Чароит, если правка графа включена и перенос сверен; "
    "иначе его применяет человек. Поручения минуток под "
    "ошибочной меткой сними в «## Снятые поручения» и верни с верным именем в "
    "«## Восстановленные поручения». Метки верны — раздел не пиши.\n")

#: Хвост строки журнала, когда раздел применяет человек (режим чтения, несверенный
#: перенос): правило то же, что у машины, — по очереди обмен сливает дорожки.
HUMAN_NOTE = "строки применять одновременно, не по очереди"


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


class NameGuard(typing.NamedTuple):
    """Что plan() не отдаёт чужой дорожке и с какой дорожки не снимает.

    `mic` — метки канала микрофона: прежние причины, и как метка, и как цель.
    `owner` — полное имя владельца из конфига: другой дорожке не отдаём
    (своя причина), и дорожку с этим именем не переименовываем, если это
    не сам канал (канал уже в `mic`).
    `bare` — первое слово полного имени, когда оно короче полного. Голое
    имя просит фамилию; тёзка с другой фамилией сюда не попадает.
    Множество строк — прежний контракт: всё это метки микрофона.
    """
    mic: frozenset[str] = frozenset()
    owner: str = ""
    bare: str = ""


def guard_for(cfg: dict) -> NameGuard:
    """Защита имён из конфига: канал микрофона, полное имя, голое первое слово."""
    sufler = cfg.get("sufler") or {}
    owner = str(sufler.get("user_name") or "").strip()
    mic = frozenset({channel_labels.mic_label_for(cfg), channel_labels.NEUTRAL_MIC})
    first = owner.split()[0] if owner else ""
    bare = first if first and first != owner else ""
    return NameGuard(mic=mic, owner=owner, bare=bare)


def _as_guard(protected: set[str] | NameGuard) -> NameGuard:
    if isinstance(protected, NameGuard):
        return protected
    return NameGuard(mic=frozenset(protected or ()))


def _label_reason(label: str, guard: NameGuard) -> str:
    if label in guard.mic:
        return REASON_MIC_LABEL
    if guard.owner and label == guard.owner:
        return REASON_OWNER_LABEL
    if guard.bare and label == guard.bare:
        return REASON_BARE_LABEL
    return ""


def _target_reason(name: str, guard: NameGuard) -> str:
    # «Я» как первое слово «Я Фамилия» — это метка микрофона, не просьба
    # дописать фамилию. Полное имя владельца проверяем раньше метки канала:
    # при имени из двух слов канал и есть это полное имя, а причина у цели
    # всё равно про имя, не про железо.
    if guard.bare and name == guard.bare and name not in guard.mic:
        return REASON_BARE
    if guard.owner and name == guard.owner:
        return REASON_OWNER
    if name in guard.mic:
        return REASON_MIC_TARGET
    return ""


def plan(fixes: list[tuple[str, str, str]], headers: set[str], protected: set[str] | NameGuard,
         dropped: list[str] | None = None) -> dict[str, str]:
    """Метка → имя, что реально применимо. Не применяется: та же или пустая
    метка; метка микрофона владельца (канал — факт железа, не догадка
    модели) — ни как метка, ни как цель (иначе чужая дорожка стала бы
    владельцем, DS r1 I4 / GLM r1 M5 по #548); голое первое слово имени
    владельца и его полное имя на чужой дорожке — своими причинами, защита
    та же; имя-заглушка («Собеседник 3»), мусор или слишком длинное; метки
    нет в заголовках реплик; вторая правка той же метки; имя — метка ДРУГОЙ
    живой дорожки, которую никто не переименовывает (слияние двух голосов
    в одного человека без отката — не делаем, критика DS r2; обмен A↔B при
    этом применим: обе дорожки переименованы одним проходом). Причина — в
    `dropped`. `protected` — множество меток микрофона (прежний контракт)
    или NameGuard.

    «�» в имени — всегда отказ, каким бы целым ни был файл ревизии: автором
    такой символ в имени не бывает, а приходит он из испорченного источника,
    который модель процитировала. Имя уезжает в заголовки реплик, в строку
    участников и в узел Люди — оттуда его уже не достать (DS r3 Critical 2;
    гейт по способу чтения был ошибкой круга 2)."""
    mapping: dict[str, str] = {}
    labels = {label for label, _, _ in fixes}
    guard = _as_guard(protected)
    for label, name, _why in fixes:
        reason = ""
        if label == name:
            reason = "то же имя"
        elif not label:
            reason = "пустая метка"
        elif (why := _label_reason(label, guard)):
            reason = why
        elif (why := _target_reason(name, guard)):
            reason = why
        elif _MANGLED_NAME in name:
            reason = "в имени нечитаемый байт, ревизию читали с заменой"
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
    # отклонён, а «А → Б» применился бы слиянием (DS r2 I1, GLM r2 M1). До
    # неподвижной точки: снятое «Б → В» делает «А → Б» слиянием в живую «Б»,
    # один проход этого не видел (аудит 13.09, GLM M1 по зоне контроля).
    while True:
        stale = [k for k, v in mapping.items() if v in headers and v not in mapping]
        if not stale:
            return mapping
        for label in stale:
            if dropped is not None:
                dropped.append(f"«{label} → {mapping[label]}» — имя — метка другой дорожки «{mapping[label]}», её правка отклонена")
            del mapping[label]


def _plain(text: str) -> str:
    """Одна строка без жирного: `**метка** → **имя**` снова стала бы _FIX."""
    return " ".join(text.replace("**", "").split())


def refusal_lines(dropped: list[str] | None) -> list[str]:
    """Строки раздела «## Не применено» из отказов plan() и прочего dropped_n.

    Проигранная гонка записи (LostRace) — своё событие журнала, не строка
    раздела. Форма plan() «метка → имя» — причина становится
    «- метка → имя — причина» без жирного. Остальное — той же строкой,
    тоже без жирного: список dropped_n человек видит целиком.
    """
    out: list[str] = []
    for item in dropped or []:
        text = (item or "").strip()
        if not text or text.startswith(review_bridge.LostRace.PREFIX):
            continue
        m = _REFUSAL.match(text)
        if m:
            label, name, why = (_plain(m.group(g)) for g in ("label", "name", "why"))
            out.append(f"- {label} → {name} — {why}")
        elif plain := _plain(text):
            out.append("- " + plain)
    return out


def render_unapplied(text: str, rows: list[str]) -> str:
    """Заменить раздел «## Не применено» по заголовку. Пустой список — снять.

    Второй такой заголовок не остаётся: повторная обработка не дописывает
    раздел заново. Строки вне раздела не трогаются.
    """
    ending = text.endswith("\n")
    body = text[:-1] if ending else text
    lines = body.split("\n") if body else []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if _UNAPPLIED_HEAD.match(lines[i]):
            end = i + 1
            while end < len(lines) and not _ANY_HEAD.match(lines[end]):
                end += 1
            spans.append((i, end))
            i = end
        else:
            i += 1
    if not rows:
        if not spans:
            return text
        for start, end in reversed(spans):
            del lines[start:end]
        while lines and lines[-1] == "":
            lines.pop()
        new = "\n".join(lines)
        if new or ending:
            return new + "\n"
        return ""
    block = [UNAPPLIED_TITLE, *rows]
    if not spans:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(block)
    else:
        for start, end in reversed(spans[1:]):
            del lines[start:end]
        start, end = spans[0]
        lines[start:end] = block
    return "\n".join(lines) + "\n"


def record_unapplied(path: pathlib.Path, dropped: list[str] | None) -> int:
    """Записать или снять «## Не применено» в файле ревизии. Число строк.

    Запись — через safe_write (гейт по снимку, две попытки). Текст не
    изменился — файл не трогаем, число всё равно возвращаем: журналу оно
    нужно и на повторном прогоне с теми же отказами.
    """
    rows = refusal_lines(dropped)

    def transform(text: str) -> tuple[str, int]:
        new = render_unapplied(text, rows)
        if new == text:
            return text, 0
        return new, 1

    safe_write.rewrite_file(path, transform, "раздел «Не применено» не записан")
    return len(rows)


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
    руками стенограмму пересборка и дальше должна считать ручной. Файл,
    сменившийся между чтением и записью (пересборка, редактор), не
    затирается: вторая попытка, затем review_bridge.LostRace (аудит зон
    12.09, зона 4; DS I3 по #553)."""
    state: dict = {}

    def transform(text: str) -> tuple[str, int]:
        fixed, n = rename_headers(text, mapping)
        if n:
            state.update(owned=_machine_owned(live, "transcript_sha256", text), before=text, fixed=fixed)
        return fixed, n

    n = review_bridge.rewrite_file(live, transform, "заголовки реплик не тронуты")
    if n:
        # .prev — ПОСЛЕ удачной записи и текстом той попытки, что записалась:
        # внутри transform вторая попытка перезаписывала его чужой версией, а
        # при проигранной гонке исходник терялся (DS M1, круг 2 по #553)
        _keep_prev(live.parent, live.name, state["before"])
        if state.get("owned"):
            live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(state["fixed"]))
    return n


def _keep_prev(folder: pathlib.Path, name: str, text: str) -> None:
    """Версия до правки — в .prev/ (одно поколение, как у пересборки)."""
    prev_dir = folder / ".prev"
    prev_dir.mkdir(exist_ok=True)
    safe_write.write_text(prev_dir / name, text)


def restamp_minutes(live: pathlib.Path, mapping: dict[str, str]) -> bool:
    """Строка участников минуток рядом со стенограммой; хеш машинных минуток
    обновляется тем же правилом, что у стенограммы. Нет минуток или строки —
    False; сменившийся под рукой файл не затирается — тот же гейт и повтор,
    что у моста поручений, затем review_bridge.LostRace."""
    mpath = review_bridge.minutes_path(live)
    if not mpath.is_file():
        return False
    state: dict = {}

    def transform(text: str) -> tuple[str, int]:
        fixed = rename_participants(text, mapping)
        if fixed == text:
            return text, 0
        state.update(owned=_machine_owned(live, "minutes_sha256", text), before=text, fixed=fixed)
        return fixed, 1

    if not review_bridge.rewrite_file(mpath, transform, "участники не тронуты"):
        return False
    _keep_prev(live.parent, mpath.name, state["before"])      # версия до правки — как у пересборки (DS r1 I3)
    if state.get("owned"):
        live_sidecar.remember(live, "minutes_sha256", live_sidecar.sha(state["fixed"]))
    return True


def planned(review: pathlib.Path, live: pathlib.Path, cfg: dict,
            dropped: list[str] | None = None) -> dict[str, str]:
    """Карта «метка → имя», которую ревизия просит применить, без правки
    файлов. Нужна и без права правки графа: мост поручений считает
    участников по НЕпереименованной стенограмме, и восстановленный пункт с
    верным именем получал бы «⚠ не участник» (DS r2 I2 по #548) — верные
    имена из этой карты мост добавляет к участникам. Файл не в UTF-8
    или стенограмма не прочиталась — пусто со строкой в `dropped`, не
    молчаливое «правок нет»: иначе повтор снял бы раздел отказов."""
    try:
        text, _lossy = review_bridge.read_review(review)
        speech = live.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        if dropped is not None:
            dropped.append(f"{live.name} не в UTF-8 — имена не перештампованы ({e.reason})")
        return {}
    except OSError as e:
        # Молчаливое «правок нет» снимало бы «## Не применено» на повторном
        # прогоне: раздел не разбирался, а журнал сказал бы «неприменённых: 0».
        if dropped is not None:
            dropped.append(f"{live.name}: стенограмма не прочитана — имена не "
                           f"перештампованы ({e.strerror or e})")
        return {}
    fixes = name_fixes(text, dropped=dropped)
    if not fixes:
        return {}
    headers = {b["speaker"] for b in transcript.parse_blocks(speech)}
    return plan(fixes, headers, guard_for(cfg), dropped=dropped)


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
    # стенограмма сменилась под рукой — ничего не применено, LostRace идёт
    # вызывающему целиком: «исправлено: … — заголовков 0» в логе было бы ложью
    # (GLM M5 по #553)
    n = restamp_transcript(live, mapping)
    mpath = review_bridge.minutes_path(live)
    touched = False
    try:
        touched = restamp_minutes(live, mapping)
    except review_bridge.LostRace as e:
        # стенограмма уже перештампована, минутки — нет: факт по файлам, сигнал
        # вызывающему строкой с LostRace.PREFIX (не «отброшенная строка раздела»)
        if dropped is not None:
            dropped.append(str(e))
    except UnicodeDecodeError as e:
        if dropped is not None:
            dropped.append(f"{mpath.name} не в UTF-8 — участники минуток не перештампованы ({e.reason})")
    except OSError as e:
        if dropped is not None:
            dropped.append(f"{mpath.name}: участники минуток не перештампованы ({e})")
    return mapping, n, touched
