"""Кто занимает машину: общие сигналы для ночи, мутатора и прочих тяжеловесов.

Ночь 23→24.08: мутатор делил локальную модель со встречей и ночным циклом —
досье поймали 35 ReadTimeout по 300 с, прогон оборвали руками. Здесь общий
словарь занятости: живая запись, разбор встреч, мутация тестов, ночной цикл.

Лок мутации — fcntl.flock по образцу live_gate.daemon.lock (круг-1 по
PR #399, DeepSeek: pid+mtime+STALE-велосипед дал три дыры — неэксклюзивный
захват, протухание на долгом базовом прогоне, pid-reuse; flock закрывает
весь класс: захват атомарен, смерть процесса освобождает ядром). С №444 B
лок разделяемый: мутаторов может идти несколько сразу (`--jobs` и соседние
прогоны), и «мутация идёт» значит «лок держит хоть один из них».
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import pathlib
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import file_locks  # noqa: E402
import live_gate  # noqa: E402
import privacy  # noqa: E402
import yaml  # noqa: E402
from charoite_graph import net  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

LOCK_REL = pathlib.Path("logs") / "mutation.lock"
NIGHTLY_REL = pathlib.Path("logs") / "nightly.json"
#: nightly.json со state=running старше этого — брошенный (ребут посреди ночи)
NIGHT_STALE_S = 8 * 3600
CONFIG_REL = pathlib.Path("config") / "config.yaml"
#: Порог «машина занята» по моделям в памяти Ollama (№595) — правило в
#: `_resident_limit`. Mac 64 ГиБ → 11,5 ГБ (десятичные, как в `ollama ps`):
#: 35b (22,5 ГБ) и 32b (~20 ГБ) — занято, эмбеддер bge-m3 (0,7–1,2 ГБ) — нет.
#: Своп 11,7 ГБ 02.10 — вторая большая модель поверх первой.
RESIDENT_BUSY_SHARE = 1 / 6
RESIDENT_BUSY_FLOOR = 8 * 10**9
#: Потолок переписи моделей: `/api/ps` отвечает за 10–20 мс и во время загрузки
#: модели (замеры 17.10 и 21.10); дольше — сервер не в себе, и это «не читается».
RESIDENT_TIMEOUT_S = 2.0


def live_recording(root: pathlib.Path) -> bool:
    """Идёт ли живая запись встречи (лок демона)."""
    try:
        return bool(live_gate.daemon_alive(root))
    except Exception:  # noqa: BLE001 — сигнал занятости не смеет ронять
        return False


def night_running(root: pathlib.Path) -> bool:
    """Идёт ли ночной цикл (logs/nightly.json, state=running, свежий)."""
    return night_busy_reason(root) is not None


def night_busy_reason(root: pathlib.Path) -> str | None:
    """Слова занятости ночным циклом — или None, если ночь не идёт.

    Несёт путь файла статуса и сколько минут назад он обновлялся: брошенный
    `running` до `NIGHT_STALE_S` (ребут посреди ночи) иначе не отличить от
    идущей ночи, и человек ждёт конца, которого не будет (№622 B2, часть 1)."""
    path = root / NIGHTLY_REL
    try:
        st = path.stat()
        # Возраст не бывает отрицательным: время правки в будущем (сбитые часы)
        # дало бы «обновлён -5 мин назад» (№622 B2, часть 1).
        age_s = max(0.0, time.time() - st.st_mtime)
        if age_s > NIGHT_STALE_S:
            return None
        state = json.loads(path.read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError):
        return None
    if state != "running":
        return None
    return f"ночной цикл ({path}, обновлён {int(age_s // 60)} мин назад)"


def mutation_running(root: pathlib.Path) -> bool:
    """Держит ли лок мутатора хоть один прогон.

    Мутаторы держат лок разделяемо, и проба разделяемым (`held_by_someone`)
    их не видит — поэтому проба эксклюзивом, `held_by_anyone`. «Мутация идёт»
    — только когда flock честно отказал; нет файла, прав или flock на томе —
    судить не по чему, ночь вставать не должна.
    """
    try:
        f = (root / LOCK_REL).open("r")
    except OSError:
        return False
    with f:
        return file_locks.held_by_anyone(f)


def resident_models(root: pathlib.Path) -> tuple[list[tuple[str, int, str]], str | None]:
    """Какие модели локальная Ollama держит в памяти: `(модели, None)` или `([], причина)`.

    Перепись сервера, а не объявления клиентов (№595): чат приложения и сторонние
    стенды аренд не пишут, а память занимают — 02.10 и 18.10 сторож отвечал
    «тихо» при резидентной 32b/35b. Три исхода, не два:
    - сигнала нет — `([], None)`: у корня нет своего `config/config.yaml` (голый
      каталог, тест, CI — без фолбэка на пример, иначе тест спросил бы живую
      Ollama владельца), движок mlx-server, адрес не этой машины или не разрешён
      (`/api/ps` чужого сервера о нашей памяти ничего не говорит), сервер не
      слушает (моделей нет), 404 на `/api/ps` (по адресу не Ollama);
    - модели — `([(имя, байт, срок словами)], None)`;
    - не читается — `([], причина)`: таймаут, иной не-200, обрыв ответа, тело без
      `models`/`size`, битый конфиг, раздел `llm` не словарь, неизвестный движок.
      Сломанный сигнал — не свободная машина.
    Адрес — только из `privacy.llm_base_url`: сырой `llm.base_url` проект не читает.
    Считается только Ollama: модель движка mlx-server живёт отдельным процессом
    без переписи и здесь не видна (долг №615).
    """
    cfg, problem = _census_config(root)
    if problem or cfg is None:
        return [], problem
    # mlx-server держит модель своим процессом — переписи у него нет (долг №615).
    # Облако — не повод молчать: без тумблера, под рубильником и в запасном пути
    # чат сидит на Ollama по `llm_base_url` (llm.py, `chat_model_url`).
    if privacy.llm_engine(cfg) == "mlx-server":
        return [], None
    try:
        base = privacy.llm_base_url(cfg)
    except privacy.PrivacyRefused:
        return [], None  # адрес запрещён политикой — не наша память
    if not privacy.is_loopback_url(base):
        return [], None
    url = base.rstrip("/") + "/api/ps"
    try:
        with net.open_url(urllib.request.Request(url), timeout=RESIDENT_TIMEOUT_S) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], None  # по адресу не Ollama (или Ollama без переписи) — сигнала нет
        return [], f"{url}: HTTP {e.code}"
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return [], None
        return [], f"{url}: {e.reason}"
    except ConnectionRefusedError:
        return [], None
    except (OSError, socket.timeout, ValueError, http.client.HTTPException) as e:
        return [], f"{url}: {type(e).__name__}"
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return [], f"{url}: в ответе нет списка models"
    out = []
    for m in models:
        size = m.get("size") if isinstance(m, dict) else None
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return [], f"{url}: у модели нет размера"
        out.append((str(m.get("name") or "?"), size, _until_words(m.get("expires_at"))))
    return out, None


def _census_config(root: pathlib.Path) -> tuple[dict | None, str | None]:
    """Свой конфиг корня данных для переписи: `(cfg, None)`; нет файла — `(None, None)`;
    сломан — `(None, причина)`: не читается, не словарь, раздел `llm` не словарь,
    неизвестный движок. Отсутствие раздела `llm` — не поломка: умолчание Ollama."""
    path = root / CONFIG_REL
    if not path.is_file():
        return None, None
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError) as e:
        return None, f"конфиг {path} не читается: {type(e).__name__}"
    if not isinstance(cfg, dict):
        return None, f"конфиг {path} не словарь"
    llm = cfg.get("llm")
    if llm is not None and not isinstance(llm, dict):
        return None, f"конфиг {path}: раздел llm не словарь"
    try:
        privacy.llm_engine(cfg)
    except privacy.PrivacyRefused as e:
        return None, f"конфиг {path}: {e}"
    return cfg, None


def resident_config_problem(root: pathlib.Path) -> str | None:
    """Поломка конфига, из-за которой перепись не прочесть, — или None.
    Не переходящая занятость: ждать её конца нечего, чинить надо конфиг —
    установщик отказывает по ней сразу, а не кодом «занято» (№595 out4)."""
    return _census_config(root)[1]


def _until_words(expires: object, now: dt.datetime | None = None) -> str:
    """Срок выгрузки словами по местному времени: «до 17:39», другой день —
    «до 22.10 09:15», бессрочно (Ollama пишет год далеко вперёд) — «без срока»."""
    if not isinstance(expires, str):
        return ""
    now = now or dt.datetime.now().astimezone()
    try:
        when = dt.datetime.fromisoformat(expires).astimezone(now.tzinfo)
    except (ValueError, OverflowError):
        return ""
    if when.year > now.year + 1:
        return "без срока"
    return f"до {when:%H:%M}" if when.date() == now.date() else f"до {when:%d.%m %H:%M}"


def _physical_bytes() -> int | None:
    try:
        n = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        return None
    return n if n > 0 else None


def resident_busy_reason(root: pathlib.Path) -> str | None:
    """Слова «модели держат память» или «память моделей не читается» — или None:
    держат меньше порога либо сигнала нет (`resident_models`).
    Не бросает: сбой сенсора — «не читается», а не «свободно» и не падение
    вызывающего (мутатор зовёт его между мутантами напрямую)."""
    try:
        return _resident_busy_reason(root)
    except Exception as e:  # noqa: BLE001 — сломанный сенсор памяти не значит «свободно»
        return f"память моделей не читается ({type(e).__name__}) — проверьте Ollama"


def _resident_limit(total: int) -> float:
    """Порог занятости: доля памяти, но не ниже пола; пол — не выше половины
    памяти (8 ГиБ: 4,3 ГБ, а не 8 — модель в 7 ГБ там уже своп)."""
    return max(total * RESIDENT_BUSY_SHARE, min(RESIDENT_BUSY_FLOOR, total / 2))


def _resident_busy_reason(root: pathlib.Path) -> str | None:
    if problem := resident_config_problem(root):
        return f"память моделей не читается ({problem}) — почините {CONFIG_REL}"
    models, problem = resident_models(root)
    if problem:
        return f"память моделей не читается ({problem}) — проверьте Ollama"
    if not models:
        return None
    held = sum(size for _, size, _ in models)
    total = _physical_bytes()
    if total is None:
        if held < RESIDENT_BUSY_FLOOR:
            return None
        return "память моделей не читается (объём памяти машины не определён)"
    limit = _resident_limit(total)
    if held < limit:
        return None
    gb = lambda n: f"{n / 1e9:.1f}".replace(".", ",")  # noqa: E731
    ordered = sorted(models, key=lambda m: -m[1])
    names = ", ".join(f"{name} {gb(size)} ГБ" + (f" {until}" if until else "")
                      for name, size, until in ordered)
    return (f"локальные модели держат память ({names}; всего {gb(held)} ГБ при пороге "
            f"{gb(limit)} ГБ) — освободить сразу: ollama stop {ordered[0][0]}")


def machine_busy(root: pathlib.Path, *, count_mutation: bool = True,
                 count_resident: bool = True) -> list[str]:
    """Чем занята машина, глазами тяжёлого процесса перед стартом.

    `count_mutation=False` — для самого мутатора: другой мутатор ему не помеха
    (замер №444 B: четыре доли разом на одном `.git` — 3,3× быстрее, выжившие те
    же), а запись, разбор встречи и ночь — помеха. Остальные читатели считают
    мутацию, как прежде.

    `count_resident=False` — модели в памяти Ollama не считать (№595); по
    умолчанию считают все: вторая большая модель или тяжёлый прогон поверх
    резидентной 35b уводят машину в своп."""
    busy: list[str] = []
    if live_recording(root):
        busy.append("живая запись")
    try:
        # Стадия разбора — словами: сырое имя стадии («transcribe») читателю
        # ничего не говорит, а причина отказа установщика обязана быть понятной.
        busy += [f"разбор встречи ({stage})" for stage in MeetingStatusStore(root).busy()]
    except Exception:  # noqa: BLE001
        pass
    night = night_busy_reason(root)
    if night:
        busy.append(night)
    if count_resident and (resident := resident_busy_reason(root)):
        busy.append(resident)
    if count_mutation and mutation_running(root):
        busy.append("мутация тестов")
    return busy


class MutationLock:
    """Разделяемый flock на время прогона мутатора: держателей может быть
    несколько, а читатели (`mutation_running`) видят любого из них.

    fd живёт в объекте весь прогон: закрытие (или смерть процесса —
    kill -9, ребут) освобождает лок ядром, сердцебиение не нужно. pid в
    файл не пишется: держателей несколько, а строка у файла одна.
    """

    def __init__(self, root: pathlib.Path):
        self.path = root / LOCK_REL
        self._f = None

    def acquire(self) -> bool:
        """True — лок наш; False — ретраи против микросекундной пробы
        эксклюзивом кончились или ФС без flock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+")
        # Ретраи против микросекундных проб эксклюзивом (`held_by_anyone`) —
        # в хелпере (круг-2 по PR #399, DS); ФС без flock — отказ сразу.
        if not file_locks.acquire_shared(f):
            f.close()
            return False
        os.chmod(self.path, 0o600)   # политика приватных каталогов, как у демона
        self._f = f
        return True

    def release(self) -> None:
        if self._f is not None:
            try:
                self._f.close()   # закрытие fd снимает flock
            finally:
                self._f = None
