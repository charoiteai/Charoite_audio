"""Tier 3: ревизия ядер графа — дубли и вложения через bge-m3 + NLI.

Экстрактор создаёт ядра по названию из каждой встречи, и сквозная тема со
временем расщепляется на двойников с размазанной хроникой. Модуль находит
такие пары и наводит порядок. Работает в двух режимах:

- ИНКРЕМЕНТАЛЬНО из graph_updater после каждой встречи: сравниваются только
  ядра, затронутые ЭТОЙ встречей, против всех (O(k×n), десятки секунд даже
  на большом графе) — граф чинится сам, руками ничего запускать не надо.
- ПОЛНЫЙ прогон всех пар — ночная launchd-джоба и CLI scripts/tier3_cores.py.

Почему два уровня отбора. difflib по словам здесь слеп: реальные дубли
(«Настройка доступа к API» ↔ «Получение токена» — одна тема разными словами; бывает и одно имя, по-разному услышанное STT) имеют word-ratio 0.0. Кандидатов отбирает bge-m3 (Ollama, батч),
судит каждую пару NLI (src/nli.py). Категории:

  ДУБЛЬ     обоюдное следование ≥ 0.80 → слить: хроника объединяется,
            статус берётся свежий, от дубля остаётся redirect. Требует
            права apply; если суть у обоих взята из автостатуса —
            вдобавок ≥ 0.92 и непересекающиеся хроники (см. revise)
  ДУБЛЬ?    обоюдное 0.72–0.80 → обратимая пометка «возможный дубль»
            в оба файла, сливает человек (осторожный режим автомата)
  ВЛОЖЕНИЕ  одна сторона ≥ 0.85, другая < 0.5 → НЕ сливать: эпизод и
            процесс — осмысленно разные узлы; вписать взаимные ссылки
  ГРАНИЦА   обе ≥ 0.45 → только в отчёт

Страховки автомата: перед любой правкой файлы копируются в
Ядра/.tier3_backup/<дата>/; генерическое ядро-«хаб», к которому NLI
притянул ≥ 3 вложений («Статус проекта»), не трогается автоматически;
нет NLI-модели или Ollama — ревизия молча пропускается, пайплайн живёт.
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import re
import shutil

import llm
import live_gate
import nli

REPR_LIMIT = 350          # NLI держит 512 токенов на пару — имя+суть с запасом
EMB_PREFILTER = 0.55      # косинус bge-m3; ниже — пары даже не судим
DUP_T = 0.72              # обоюдное следование → похоже на дубль
# ОСТОРОЖНЫЙ РЕЖИМ автомата (cautious mode): деструктивное слияние —
# только при кристальной уверенности с ОБЕИХ сторон; зона DUP_T..MERGE_T
# получает обратимую пометку «возможный дубль» в оба файла, сливает человек.
MERGE_T = 0.80
# Планка для пары, у которой суть с ОБЕИХ сторон взята из автостатуса.
# «## Суть» пишет человек и пишет про тему; статус переписывает каждая
# встреча, и у двух активных задач одного проекта он похож сам по себе
# («ждём аудит», «в очереди»). Основание слабее — планка выше; запретить
# такие слияния совсем нельзя: у ядра от upsert_core «## Сути» нет никогда,
# и запрет выключил бы --apply целиком.
MERGE_T_WEAK = 0.92
CHRON_OVERLAP = 0.5       # доля общих встреч, после которой пару не сливаем
NEST_HI, NEST_LO = 0.85, 0.5
HUB_LIMIT = 3             # ≥ стольких вложений в одно ядро → хаб, не трогаем
BACKUP_KEEP = 20          # держим столько последних бэкап-папок


_UPDATED = re.compile(r"_?\(обновлено \d{4}-\d{2}-\d{2}\)_?")
_CHRON_LINK = re.compile(r"^- \[\[([^\]|#]+)")


def _plain(status: str) -> str:
    """Статус без служебной метки даты — на вход NLI идёт смысл, не разметка."""
    return " ".join(_UPDATED.sub("", status).split())


def _meeting_overlap(a: dict, b: dict) -> float:
    """Доля общих встреч в хронике меньшего из двух ядер.

    Независимый от текста довод. Двойники рождаются из РАЗНЫХ встреч: одну и
    ту же тему экстрактор в июне назвал одним именем, в июле другим, и хроники
    у них дополняют друг друга. Два живых потока одного проекта, наоборот,
    ведут на одних и тех же встречах — и сегодняшний статус у них общий на
    вид. Поэтому пересечение хроник — довод ПРОТИВ слияния.
    """
    if not a["meetings"] or not b["meetings"]:
        return 0.0
    common = a["meetings"] & b["meetings"]
    return len(common) / min(len(a["meetings"]), len(b["meetings"]))


def changed_since(folder: pathlib.Path, since: float) -> list[str]:
    """Имена ядер, изменённых после отметки времени — фокус ночного инкремента.

    Полный прогон квадратичен по числу ядер, и на выросшем графе это часы:
    293 файла ядер — уже сорок тысяч пар, каждую кандидатскую судит NLI в один
    поток. Ночью такой прогон съедает всё окно и до утреннего брифа очередь не
    доходит. Между тем свежих ядер за сутки — единицы, а revise(only_names=...)
    умеет сравнивать именно их против всех (O(k×n)).

    Отметка берётся на НАЧАЛЕ прогона, а не в конце: за время ревизии встреча
    могла обновить ядро, и метка «конец» такую правку потеряла бы навсегда.
    Плата — собственные пометки прошлой ночи попадают в фокус ещё один раз;
    это самоограничивается, потому что _mark_dup/_link идемпотентны и второй
    раз файл не трогают.
    """
    if not folder.is_dir():
        return []
    return [p.stem for p in sorted(folder.glob("*.md"))
            if not p.name.startswith("_") and p.stat().st_mtime > since]


def load_cores(folder: pathlib.Path) -> list[dict]:
    cores = []
    for p in sorted(folder.glob("*.md")):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8")
        if "Дубль. Смерджен" in text:
            continue  # уже сведён прошлой ревизией
        def sect(title: str) -> str:
            m = re.search(rf"## {title}\n(.*?)(?=\n## |\Z)", text, re.S)
            return " ".join(m.group(1).split()) if m else ""
        status = sect("Статус")
        # «## Суть» пишет только человек: upsert_core собирает ядро из
        # «## Статус» и «## Хроники». Без запасного варианта repr сводился к
        # имени файла, и NLI судил пары голых заголовков — ровно та слепота,
        # ради ухода от которой сюда и ставили эмбеддинги с NLI.
        written = sect("Суть") or sect("Задача одной фразой")
        essence = written or _plain(status)
        dm = re.search(r"обновлено (\d{4}-\d{2}-\d{2})", status)
        chron = re.findall(r"^- \[\[.*", text, re.M)
        cores.append({
            "path": p, "name": p.stem, "status": status, "essence": essence,
            # откуда взялась суть: формулировка темы или сегодняшний автостатус.
            # По этому полю revise выбирает планку для необратимого слияния
            "essence_src": "секция" if written else "статус",
            # встречи хроники без подписи и хвоста «— что изменилось»:
            # одну встречу два ядра описывают разными словами, сравнивать
            # надо ссылку, а не строку
            "meetings": {m.group(1) for ln in chron
                         if (m := _CHRON_LINK.match(ln))},
            "chron": chron, "text": text,
            "date": dm.group(1) if dm else "",
            "repr": f"{p.stem}. {essence}"[:REPR_LIMIT],
        })
    return cores


def _embed_all(cores: list[dict], cfg: dict) -> list[list[float]]:
    # Адрес и транспорт — через llm.embed (единая точка): прежний хардкод
    # 127.0.0.1:11434 игнорировал llm.base_url из конфига (аудит 14.08).
    return llm.embed(cfg, [c["repr"] for c in cores],
                     model="bge-m3", keep_alive="60m", timeout=120)


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def _backup(folder: pathlib.Path, stamp: str, *files: pathlib.Path) -> None:
    """Копия правимых файлов: автомат без бэкапа — не автомат, а рулетка."""
    bdir = folder / ".tier3_backup" / stamp
    bdir.mkdir(parents=True, exist_ok=True)
    for f in files:
        dst = bdir / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
    # старые бэкапы не копим бесконечно
    root = folder / ".tier3_backup"
    old = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name)
    for d in old[:-BACKUP_KEEP]:
        shutil.rmtree(d, ignore_errors=True)


def _merge(folder: pathlib.Path, stamp: str, a: dict, b: dict, log: list[str]) -> None:
    """Слить дубль: канон — у кого хроника длиннее (при равной — свежий статус)."""
    canon, dup = (a, b) if (len(a["chron"]), a["date"]) >= (len(b["chron"]), b["date"]) else (b, a)
    _backup(folder, stamp, canon["path"], dup["path"])
    text = canon["text"]
    if dup["date"] > canon["date"] and dup["status"]:
        text = re.sub(r"## Статус\n.*?(?=\n## |\Z)",
                      f"## Статус\n{dup['status']}\n\n", text, 1, re.S)
    have = set(canon["chron"])
    extra = [ln for ln in dup["chron"] if ln not in have]
    if extra:
        if "## Хроника" in text:
            text = re.sub(r"(## Хроника\n)", "\\1" + "\n".join(extra) + "\n", text, 1)
        else:
            text += "\n## Хроника\n" + "\n".join(extra) + "\n"
    # просьба «свести вручную» на эту же пару отработала: снимаем, иначе
    # morning_brief будет вечно звать человека сводить уже сведённое
    text = re.sub(rf"\n> ⚠️ Tier3-NLI: возможный дубль: "
                  rf"\[\[Ядра/{re.escape(dup['name'])}\|.*?\n", "\n", text)
    text += f"\n> 🔀 Tier3-NLI: сюда влита хроника дубля «{dup['name']}».\n"
    canon["path"].write_text(text, encoding="utf-8")
    canon["text"] = text
    dup["path"].write_text(
        f"---\ntype: ядро\nвид: задача\ntags: [дубль, redirect, tier3-nli]\n---\n"
        f"# {dup['name']} → [[Ядра/{canon['name']}]]\n\n"
        f"⚠️ **Дубль. Смерджен Tier3-NLI.** Хроника перенесена в "
        f"[[Ядра/{canon['name']}|{canon['name']}]].\n",
        encoding="utf-8")
    log.append(f"🔀 «{dup['name']}» → «{canon['name']}» (+{len(extra)} строк хроники)")


def _mark_dup(folder: pathlib.Path, stamp: str, a: dict, b: dict, log: list[str]) -> None:
    """Средняя уверенность: обратимая пометка в оба файла, сливает человек."""
    changed = False
    for src, dst in ((a, b), (b, a)):
        if f"возможный дубль: [[Ядра/{dst['name']}" in src["text"]:
            continue
        _backup(folder, stamp, src["path"])
        src["text"] += (f"\n> ⚠️ Tier3-NLI: возможный дубль: "
                        f"[[Ядра/{dst['name']}|{dst['name']}]] — свести вручную, "
                        f"если это одна тема.\n")
        src["path"].write_text(src["text"], encoding="utf-8")
        changed = True
    if changed:
        log.append(f"⚠️ возможный дубль (пометка): «{a['name']}» ↔ «{b['name']}»")


def _link(folder: pathlib.Path, stamp: str, part: dict, whole: dict, log: list[str]) -> None:
    """Вложение: не сливаем, а даём графу взаимные ссылки-подсказки."""
    changed = False
    for src, dst, tag in ((part, whole, "часть более широкой темы"),
                          (whole, part, "частный эпизод этой темы")):
        if f"[[Ядра/{dst['name']}" in src["text"]:
            continue
        _backup(folder, stamp, src["path"])
        src["text"] += f"\n> 🧩 Tier3-NLI: {tag} — [[Ядра/{dst['name']}|{dst['name']}]]\n"
        src["path"].write_text(src["text"], encoding="utf-8")
        changed = True
    if changed:
        log.append(f"🧩 «{part['name']}» ⊂ «{whole['name']}»")


def auto_apply_allowed(cfg: dict) -> bool:
    """Разрешено ли автомату НЕОБРАТИМОЕ слияние (sufler.tier3_auto_apply).

    Строго is True — та же политика, что у облачных тумблеров в privacy.py:
    None, "", 0, 1, "false" строкой и прочий мусор разрешением не считаются.
    Единая точка для обоих путей к revise(apply=True) — дневного
    (graph_updater после встречи) и ночного (tier3_cores --auto).
    """
    return (cfg.get("sufler") or {}).get("tier3_auto_apply") is True


def revise(graph: pathlib.Path, only_names: list[str] | None = None,
           apply: bool = False, mark: bool = False,
           cfg: dict | None = None) -> dict:
    """Ревизия ядер графа. only_names — инкрементально (ядра этой встречи).

    Два права, а не одно, потому что цена у правок разная:

    mark  — обратимое: строка-цитата «возможный дубль» / «часть темы» в конец
            файла. Её читает morning_brief в раздел «Tier3 просит свести
            вручную». Без отдельного права выключенный автомат не осторожен,
            а нем: находка живёт только в логе одного прогона.
    apply — необратимое для пользователя: слияние перезаписывает ядро, от
            дубля остаётся redirect-заглушка (оригинал — в .tier3_backup).
            Такое право берут явно: scripts/tier3_cores.py --apply или
            sufler.tier3_auto_apply в конфиге. apply включает и mark.

    Без права на слияние уверенная пара не пропадает, а понижается до
    пометки. Понижается она и тогда, когда основание слабое: если суть у
    обоих ядер взята из автостатуса (а у ядра от upsert_core «## Сути» нет
    никогда), нужно ≥ MERGE_T_WEAK и непересекающиеся хроники. Статус
    переписывает каждая встреча, у двух живых задач одного проекта он похож
    сам по себе — и такие задачи ведут на одних и тех же встречах, а
    двойники, наоборот, приходят из разных.

    Возвращает {"dups": [...], "nests": [...], "border": [...], "log": [...],
    "pending_merges": [...], "skipped": [...]}.
    Любая инфраструктурная беда (нет модели, лежит Ollama) — пустой результат,
    НЕ исключение: ревизия — уборка, она не имеет права валить пайплайн встречи.
    """
    out: dict = {"dups": [], "nests": [], "border": [], "log": [],
                 # отработала ли ревизия на самом деле. Пустой результат
                 # означает и «чисто», и «нет NLI-модели / лежит Ollama», а для
                 # ночного инкремента разница решающая: сдвинуть отметку
                 # времени после несостоявшегося прогона — значит навсегда
                 # потерять из фокуса ядра, которые он должен был разобрать
                 "ran": False,
                 # пары, которые слил бы прогон с apply=True, а этот не слил.
                 # По этому полю (а не по факту находки) вызывающий решает,
                 # советовать ли человеку `tier3_cores.py --apply`: совет,
                 # который на его данных ничего не делает, хуже молчания
                 "pending_merges": [], "skipped": []}
    folder = graph / "Ядра"
    if not folder.is_dir() or not nli.is_available():
        return out
    cores = load_cores(folder)
    if len(cores) < 2:
        return out
    focus = ({c["name"] for c in cores} if not only_names
             else {n for n in only_names})
    try:
        # cfg передаёт graph_updater; CLI без конфига падает на дефолт
        # privacy.llm_base_url({}) — тот же локальный адрес, что раньше.
        embs = _embed_all(cores, cfg or {})
    except Exception:
        return out  # Ollama лежит — не мешаем пайплайну
    # llm.embed при ошибке сервера отдаёт `[]`, а не исключение (404 «модель
    # не найдена»): раньше это доезжало до IndexError в цикле пар и валило
    # CLI ночи (аудит DeepSeek 17.08). Неполный ответ = прогон не состоялся.
    if len(embs) != len(cores):
        return out
    # Сессию NLI поднимаем ДО того, как объявить прогон состоявшимся: при
    # битой ONNX-модели entail_prob тихо возвращает 0.0, суд «ничего не
    # находит», а ran=True двигал отметку --since-last — и свежие ядра
    # навсегда выпадали из инкремента (аудит DeepSeek 17.08).
    if not nli.ready():
        return out
    out["ran"] = True
    out["stopped"] = False

    pairs = []
    for i in range(len(cores)):
        for j in range(i + 1, len(cores)):
            if cores[i]["name"] not in focus and cores[j]["name"] not in focus:
                continue
            c = _cos(embs[i], embs[j])
            if c >= EMB_PREFILTER:
                pairs.append((c, cores[i], cores[j]))
    pairs.sort(key=lambda x: -x[0])

    dups, maybe_dups, nests = [], [], []
    for c, a, b in pairs:
        # Потолок ночи — внутри суда, а не только между графами: у типовой
        # установки граф ОДИН, и полный воскресный прогон (десятки тысяч
        # пар, NLI в один поток) шёл бы часами мимо потолка (круг-2 по
        # PR #363, GLM). Каждая пара — отдельный вызов NLI, естественная
        # точка останова; частичный результат помечается stopped, и
        # вызывающий не двигает отметку --since-last.
        if live_gate.night_is_over():
            out["stopped"] = True
            break
        try:
            ab = nli.entail_prob(a["repr"], b["repr"])
            ba = nli.entail_prob(b["repr"], a["repr"])
        except Exception:
            continue
        if ab >= MERGE_T and ba >= MERGE_T:
            weak = a["essence_src"] == "статус" and b["essence_src"] == "статус"
            if not weak:
                doubt = ""
            elif min(ab, ba) < MERGE_T_WEAK:
                doubt = f"суть из автостатуса, для слияния нужно ≥ {MERGE_T_WEAK}"
            elif _meeting_overlap(a, b) >= CHRON_OVERLAP:
                doubt = "суть из автостатуса, а ядра ведут на одних встречах"
            else:
                doubt = ""
            if doubt:
                maybe_dups.append((a, b, c, ab, ba))
                out["dups"].append(f"«{a['name']}» ↔? «{b['name']}» "
                                   f"{ab:.2f}/{ba:.2f} ({doubt} — пометка)")
            else:
                dups.append((a, b, c, ab, ba))
                out["dups"].append(f"«{a['name']}» ↔ «{b['name']}» {ab:.2f}/{ba:.2f}")
        elif ab >= DUP_T and ba >= DUP_T:
            maybe_dups.append((a, b, c, ab, ba))
            out["dups"].append(f"«{a['name']}» ↔? «{b['name']}» {ab:.2f}/{ba:.2f} (пометка)")
        elif max(ab, ba) >= NEST_HI and min(ab, ba) < NEST_LO:
            nests.append((a, b, c, ab, ba))
        elif ab >= 0.45 and ba >= 0.45:
            out["border"].append(f"«{a['name']}» ?? «{b['name']}» {ab:.2f}/{ba:.2f}")

    whole_count: dict[str, int] = {}
    for a, b, c, ab, ba in nests:
        whole = b if ab > ba else a
        whole_count[whole["name"]] = whole_count.get(whole["name"], 0) + 1
    hubs = {n for n, k in whole_count.items() if k >= HUB_LIMIT}
    for a, b, c, ab, ba in nests:
        part, whole = (a, b) if ab > ba else (b, a)
        hub_note = " [хаб — пропуск]" if whole["name"] in hubs else ""
        out["nests"].append(f"«{part['name']}» ⊂ «{whole['name']}» {ab:.2f}/{ba:.2f}{hub_note}")
        if hub_note:
            # найдено, но осознанно не тронуто — единственное, о чём стоит
            # сказать вызывающему отдельно от лога правок
            out["skipped"].append(f"«{whole['name']}» — хаб ({whole_count[whole['name']]} "
                                  f"вложений), ссылки не вписываем")

    def _pair(a: dict, b: dict) -> str:
        return f"«{a['name']}» ↔ «{b['name']}»"

    if not (apply or mark):
        out["pending_merges"] = [_pair(a, b) for a, b, *_ in dups]
        return out
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    merged: set[str] = set()
    if apply:
        for a, b, c, ab, ba in dups:
            if a["name"] in merged or b["name"] in merged:
                # ядро уже слито в этом прогоне, и его текст в памяти устарел.
                # Пару не теряем: после слияния дубль становится redirect и
                # выпадает из load_cores, так что следующий прогон разберёт её
                # на свежих файлах
                out["pending_merges"].append(_pair(a, b))
                continue
            _merge(folder, stamp, a, b, out["log"])
            merged.update((a["name"], b["name"]))
        to_mark = maybe_dups
    else:
        # права сливать нет — уверенная пара получает ту же обратимую пометку,
        # иначе находка исчезает вместе с логом прогона
        to_mark = dups + maybe_dups
        out["pending_merges"] = [_pair(a, b) for a, b, *_ in dups]
    for a, b, c, ab, ba in to_mark:
        if a["name"] in merged or b["name"] in merged:
            continue
        _mark_dup(folder, stamp, a, b, out["log"])
    for a, b, c, ab, ba in nests:
        part, whole = (a, b) if ab > ba else (b, a)
        if whole["name"] in hubs or part["name"] in merged or whole["name"] in merged:
            continue
        _link(folder, stamp, part, whole, out["log"])
    return out
