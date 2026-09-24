"""Снимки графа уезжают из iCloud и перестают дублировать байты.

Факт 21.08: граф живёт в хранилище Обсидиана, а `cloud_review` клал снимок
внутрь самого графа. Каждая облачная правка копировала граф целиком, и к
этому дню в iCloud лежало 48 122 служебных файла на 1.7 ГБ — из 58 260
файлов и 1.9 ГБ всего архива. Синхронизацию этого балласта система вела
круглосуточно (`fileproviderd` 127% CPU, плюс `bird`, `fseventsd`,
`filecoordination`), и живой записи не хватало процессора: стенограмма
отставала и рвалась.

Здесь проверяется, что снимок ложится к данным, а не в граф, и что копии
не платят за себя дважды: на APFS файлы берутся клоном (copy-on-write).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import charoite_paths  # noqa: E402
import cloud_review  # noqa: E402
# Импорт НА СБОРКЕ, а не в теле теста: свидетель ниже проверяет, что модуль
# отвечает ТЕКУЩИМ корнем даже будучи импортированным до всякой фикстуры.
# Импорт внутри теста этого не проверил бы: модуль увидел бы уже названный
# корень и зеленел бы и с возвращённым снимком.


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    g = tmp_path / "vault" / "рабочий_граф"
    (g / "Встречи").mkdir(parents=True)
    (g / "Ядра").mkdir()
    (g / "Встречи" / "2026-08-21_1103.md").write_text("узел встречи", encoding="utf-8")
    (g / "Ядра" / "Оплата.md").write_text("ядро темы", encoding="utf-8")
    return g


def test_snapshot_path_is_outside_the_graph(tmp_path):
    """Снимок обязан лежать в данных: в графе его синхронизирует iCloud."""
    g = _graph(tmp_path)
    dest = charoite_paths.graph_backups(g, "cloud_backup", root=tmp_path / "data")
    assert not dest.is_relative_to(g), "снимок остался внутри графа"
    assert dest.name == "cloud_backup", dest
    # имя + хеш полного пути: одноимённые графы из разных vault не смешиваются
    assert dest.parent.name.startswith("рабочий_граф-"), dest


def test_snapshots_of_different_graphs_do_not_mix(tmp_path):
    a = charoite_paths.graph_backups(tmp_path / "рабочий_граф", root=tmp_path / "d")
    b = charoite_paths.graph_backups(tmp_path / "дом", root=tmp_path / "d")
    assert a != b


def test_backup_graph_writes_next_to_data(tmp_path, monkeypatch):
    """Полный снимок: файлы на месте, но в корне данных, а не в графе."""
    g = _graph(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(cloud_review, "_root", lambda _к=data: _к)

    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    assert dest.is_relative_to(data), dest
    assert (dest / "Встречи" / "2026-08-21_1103.md").read_text(encoding="utf-8") == "узел встречи"
    assert (dest / "Ядра" / "Оплата.md").exists()
    assert not (g / ".cloud_backup").exists(), "в графе не должно остаться снимков"


def test_snapshot_survives_rewrite_of_the_original(tmp_path, monkeypatch):
    """Клон — независимый файл: правка графа не должна протечь в снимок.

    Ради этого и взят `clonefile`, а не жёсткая ссылка: конвейер пишет через
    `tmp.replace()` (новый inode), но человек правит те же заметки руками в
    Обсидиане, и запись на месте не запрещена никем.
    """
    g = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    node = g / "Встречи" / "2026-08-21_1103.md"
    with node.open("w", encoding="utf-8") as f:      # именно на месте, не replace
        f.write("ПЕРЕПИСАНО ЧЕЛОВЕКОМ")

    assert (dest / "Встречи" / "2026-08-21_1103.md").read_text(encoding="utf-8") == "узел встречи"


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="clonefile — только macOS/APFS; откат на copy2 "
                           "покрыт соседним тестом")
def test_clone_is_used_and_gives_an_independent_file(tmp_path):
    """Клон обязан отработать на APFS — иначе снимок опять платит за байты.

    Отличить клон от копии по `stat` нельзя: `st_blocks` у обоих одинаковые,
    разделение блоков видно только файловой системе. Проверяем прямо: вызов
    сказал «получилось», файл читается и живёт своим inode.
    """
    src = tmp_path / "a.md"
    src.write_text("строка стенограммы\n" * 20_000, encoding="utf-8")
    dst = tmp_path / "b.md"

    assert cloud_review._clone(src, dst) is True, "clonefile не отработал на этом томе"
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert dst.stat().st_ino != src.stat().st_ino


def test_copy_is_the_fallback_when_clone_fails(tmp_path, monkeypatch):
    """Не APFS, другой том, старая система — снимок всё равно полный."""
    g = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    monkeypatch.setattr(cloud_review, "_clone", lambda src, dst: False)

    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    assert (dest / "Встречи" / "2026-08-21_1103.md").read_text(encoding="utf-8") == "узел встречи"
    assert (dest / "Ядра" / "Оплата.md").read_text(encoding="utf-8") == "ядро темы"


def test_same_name_graphs_from_different_vaults_do_not_mix(tmp_path):
    """Круг по PR #363 (DeepSeek): ключ только по имени папки смешивал снимки
    одноимённых графов — ротация одного стирала срез другого."""
    a = charoite_paths.graph_backups(tmp_path / "v1" / "Работа", root=tmp_path / "d")
    b = charoite_paths.graph_backups(tmp_path / "v2" / "Работа", root=tmp_path / "d")
    assert a != b


def test_backup_graph_does_not_rotate_and_rotation_is_separate(tmp_path, monkeypatch):
    """Круг-1 и круг-2 по PR #363 (GLM + DeepSeek, Critical): ротация внутри
    backup_graph съедала снимок соседнего воркера, чья сверка ещё шла, —
    без копии enforce_boundaries вместо отката УДАЛЯЛ файлы. Теперь
    backup_graph только создаёт, а rotate_snapshots зовётся в конце run()
    и не трогает ни свой срез, ни чужие файлы."""
    g = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    root = cloud_review.backup_root(g)

    first = cloud_review.backup_graph(g, "2026-12-31_2359")   # «сосед» со штампом новее
    (root / "заметка-пользователя.txt").write_text("не снимок", encoding="utf-8")
    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    assert first.exists() and dest.exists(), (
        "backup_graph не смеет ротировать: чужая сверка ещё идёт")

    cloud_review.rotate_snapshots(root, dest)
    assert dest.exists(), "ротация съела свой срез"
    assert not first.exists(), "чужой срез не ротирован после сверки"
    assert (root / "заметка-пользователя.txt").exists(), "ротация трогает не-каталоги"




@pytest.mark.настоящий_корень_ревизии
def test_корень_ревизии_у_канона_а_не_своя_копия(tmp_path):
    """Облачная ревизия спрашивает корень у канона — седьмой копии правила нет.

    Своя копия читала `CHAROITE_ROOT` сама и теряла `strip()`/`resolve()`:
    в одном прогоне снимки графа и карантин уезжали по одному корню, а
    журнал несвязанных узлов — по другому (круг 2 по коду №327, DS I1).
    Переменную перетираем после названия корня: канон обязан ответить
    названным.
    """
    названный = charoite_paths.use_data_root(tmp_path / "данные")   # через дверь, не мимо
    os.environ["CHAROITE_ROOT"] = str(tmp_path / "перетёртый")
    assert cloud_review._root() == названный


def граф_не_изолирован(tmp_path) -> str:
    """Чем плох граф процесса; пустая строка — всё в порядке.

    Судится значение, а не факт «переменная чем-то занята»: в шелле владельца
    `CHAROITE_GRAPH_DIR` штатно экспортирован (им включают демо-граф для
    скринов), и мягкая проверка «непусто» оставалась зелёной ровно в том
    состоянии, от которого изоляция заведена — на живом графе владельца
    (круг 10 по коду №327, DS C1).

    Возвращает причину, а не ассертит сама: гейт утверждений проекта считает
    тест без `assert` в теле неспособным упасть, и прятать проверку в хелпер
    значит выключать этот гейт (`tests/test_check_test_assertions.py`).
    """
    for имя in ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR"):
        значение = os.environ.get(имя)
        if not значение:
            return f"{имя} снят — изоляции графа нет"
        if not pathlib.Path(значение).is_relative_to(tmp_path):
            return f"{имя}={значение} вне каталога теста — тест пойдёт в граф владельца"
    return ""


def test_предусловие_требует_временный_корень(tmp_path):
    """Свойство корня — само под тестом, иначе оно умрёт молча.

    Свойство простое: все корни, на которых работает тест, обязаны быть
    временными. Корень установки и живой корень данных владельца одинаково
    опасны — ревизия писала бы туда снимки и ходила по настоящим встречам.
    Судятся ВСЕ названные корни: сходивший на установку и вернувшийся в tmp
    тест иначе прошёл бы незамеченным (DS I2 круга 9).
    """
    from conftest import не_временный_корень
    assert "не назван" in не_временный_корень([])
    assert "не временный" in не_временный_корень([pathlib.Path("/установка")])
    assert "не временный" in не_временный_корень([pathlib.Path.home() / "Documents" / "Чароит"])
    assert "не временный" in не_временный_корень([tmp_path, pathlib.Path("/установка")])
    assert не_временный_корень([tmp_path]) == ""
    assert не_временный_корень([pathlib.Path(tempfile.mkdtemp())]) == ""


@pytest.mark.настоящий_корень_ревизии
def test_помеченный_тест_не_теряет_изоляцию_графа(tmp_path):
    """Маркер снимает подмену корня ревизии — и только её.

    Пока изоляция графа была хвостом той же фикстуры, ветвление по маркеру
    отрезало её целиком: помеченный тест без переменной уходил в iCloud
    владельца через `graphs.roots()` (№197 — 14 651 файл, 120 с). Изоляция
    живёт отдельной фикстурой и маркера не знает (круг 8 по коду №327, DS C1).
    """
    charoite_paths.use_data_root(tmp_path / "данные")
    беда = граф_не_изолирован(tmp_path)
    assert not беда, беда


def test_изоляция_графа_переживает_undo_в_теле_теста(monkeypatch, tmp_path):
    """Общий `monkeypatch` снимается тестом — изоляция графа не должна.

    `monkeypatch.undo()` в теле теста (так делают два теста гигиены графа)
    снимал и `CHAROITE_GRAPH_DIR`, после чего `graphs.roots()` при пустом
    конфиге уходил в iCloud владельца — №197 в чистом виде
    (круг 9 по коду №327, DS C1).
    """
    monkeypatch.setattr(os, "sep", os.sep)      # что-нибудь в стек monkeypatch
    monkeypatch.undo()
    беда = граф_не_изолирован(tmp_path)
    assert not беда, беда


def test_запрет_сети_переживает_undo_в_теле_теста(monkeypatch):
    """Сетевой гейт не снимается чужим `monkeypatch.undo()`.

    Шесть тестов репозитория зовут `undo()` посреди работы; пока гейт стоял
    на monkeypatch, после этого возвращался настоящий `requests.post` — и
    запись в живую память владельца снова становилась возможной без единого
    сигнала (круг 14 по коду №327, GLM I1). Тот же образец, что у корня и
    графа: save/restore руками.

    Ждём `pytest.fail.Exception`, а не `AssertionError`: отказ, который ловит
    `except Exception` продукта, тест не роняет (№376). Этот тип `except
    Exception` пропускает — самопроверка краснеет, стоит отказу снова стать
    обычным исключением.
    """
    import requests
    monkeypatch.setattr(os, "sep", os.sep)      # что-нибудь в стек monkeypatch
    monkeypatch.undo()
    with pytest.raises(pytest.fail.Exception, match="пошёл в сеть"):
        requests.post("http://127.0.0.1:8100/remember", json={})


def test_запрет_сети_закрывает_и_urllib():
    """Второй транспорт — stdlib: `scripts/doctor.py` спрашивает Ollama через
    `urllib.request.urlopen`, и без сторожа тест доктора шёл бы в живой сервер
    (№376). Адрес в отказе — и у строки, и у `Request`."""
    import urllib.request
    with pytest.raises(pytest.fail.Exception, match=r"пошёл в сеть \(http://localhost:11434/api/tags\)"):
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4)
    запрос = urllib.request.Request("http://127.0.0.1:8100/remember", data=b"{}")
    with pytest.raises(pytest.fail.Exception, match=r"пошёл в сеть \(http://127\.0\.0\.1:8100/remember\)"):
        urllib.request.urlopen(запрос)


def test_обвязка_называет_канону_временный_корень(tmp_path):
    """Корень данных тестового процесса назван, и назван во временном месте.

    Пока обвязка корень только отзывала, канон отвечал положением файла —
    корнем кода, то есть в рабочем checkout живыми данными владельца. Боевой
    путь `graph_updater._root() / "logs" / "brain_sent"` весь прогон указывал
    на настоящую очередь переотправки (круг 18 по коду №327, DS C1 = GLM C1).
    """
    from conftest import не_временный_корень
    корень = charoite_paths.resolve_root(charoite_paths.__file__)
    assert корень == (tmp_path / "данные").resolve(), \
        "канон отвечает не тем корнем, который назвала обвязка"
    assert не_временный_корень([корень]) == ""


def test_очередь_долгов_продукта_лежит_в_tmp():
    """Тот же вопрос с той стороны, откуда он важен: глазами продуктового кода.

    `send_to_brain` пишет `<штамп>.txt`, `.pending` и вечный `.lock` в
    `_root()/logs/brain_sent`. Проверяем не «сторож не сработал», а что
    адреса живой очереди у тестового процесса просто нет.
    """
    import graph_updater
    журнал = graph_updater._root() / "logs" / "brain_sent"
    живой = charoite_paths.CODE_ROOT / "logs" / "brain_sent"
    assert журнал != живой, "продуктовый код адресует живую очередь владельца"
    assert pathlib.Path(tempfile.gettempdir()).resolve() in журнал.resolve().parents


def test_корень_переживает_undo_в_теле_теста(monkeypatch):
    """`monkeypatch.undo()` посреди теста не возвращает корень кода.

    Именно так утекла очередь долгов в круге 15: шестнадцать тестов подменяют
    `_root` сами, один из них звал `undo()` и продолжал работать — подмена
    обвязки снималась вместе с его собственной. Публикация корня стоит не на
    `monkeypatch`, и снять её `undo()` не может.
    """
    monkeypatch.setattr(os, "sep", os.sep)      # что-нибудь в стек monkeypatch
    monkeypatch.undo()
    assert charoite_paths.resolve_root(charoite_paths.__file__) != charoite_paths.CODE_ROOT


#: Как спросить модуль о корне: у большинства `_root()`, у `nli` путь к модели,
#: у `llm` — метод класса. Сам СПИСОК модулей живёт в обвязке
#: (`tests/conftest.py`, `МОДУЛИ_БЕЗ_СНИМКА`): она импортирует их до называния
#: корня, и держать второй список здесь значило бы дать проверке разойтись с
#: тем, что обвязка реально импортировала (круг 2 по коду №329, GLM C1).
КАК_СПРОСИТЬ = {
    # спрашиваем ТЕМ ЖЕ путём, которым ходит бой: у `llm` корень наблюдается
    # через каталог аренды модели (его пишет процесс, а читает сторож
    # здоровья), а не через геттер — иначе «перенесли снимок в `__init__`»
    # осталось бы зелёным (круг 2 по коду №329, DS C3/I1, GLM I3)
    "nli": lambda м: м._dir().parent.parent,
    "llm": None,        # спрашивается ниже через движок, созданный ДО именования
}


def _след_ли(значение, прежний: pathlib.Path) -> bool:
    """Значение указывает на ПРЕЖНИЙ корень — в любом виде записи пути."""
    if isinstance(значение, bool) or not isinstance(значение, (str, os.PathLike)):
        return False
    try:
        путь = pathlib.Path(значение)
    except (TypeError, ValueError):
        return False
    return путь == прежний or прежний in путь.parents


def _следы(носитель: dict, прежний: pathlib.Path) -> dict:
    """Имена, под которыми в носителе осел прежний корень.

    Ищем по ЗНАЧЕНИЮ, а не по типу: снимок оседает не только `pathlib.Path` в
    атрибуте — строкой (`str(resolve_root(...))`) и внутри словаря или кортежа
    он тот же самый снимок, а `lease_dir()` при этом отвечает каноном и все
    прочие ассерты зеленеют (круг 5 по коду №329, GLM I4). Контейнеры
    разбираются на один уровень: глубже след уже не «атрибут объекта», а
    структура данных со своей жизнью.
    """
    найдено = {}
    for имя, v in носитель.items():
        if имя.startswith("__"):
            continue
        части = list(v.values()) if isinstance(v, dict) else list(v) if isinstance(v, (tuple, list, set)) else [v]
        попались = [ч for ч in части if _след_ли(ч, прежний)]
        if попались:
            найдено[имя] = попались if len(части) > 1 else попались[0]
    return найдено


def test_модуль_отвечает_текущим_корнем_а_не_замороженным(tmp_path):
    """Корень назвали ПОСЛЕ импорта — модуль обязан ответить новым.

    Тринадцать модулей `src/` писали `ROOT = resolve_root(__file__)` на верхнем
    уровне, а `llm.LLM` — то же самое полем класса: значение снималось на
    СБОРКЕ, раньше любой фикстуры и раньше, чем точка входа успевала назвать
    корень. Процесс разъезжался сам с собой — писатель аренды модели клал её в
    один каталог, сторож здоровья искал в другом, и «наша генерация в полёте»
    отвечало «нет» (замер 21.09, №329; поле класса нашёл круг 1, GLM C1).

    Здесь проверяется противоположное свойство: заморозки больше нет. Верните
    любому модулю из списка константу — тест покраснеет на нём поимённо.
    """
    import sys

    from conftest import МОДУЛИ_БЕЗ_СНИМКА

    from conftest import КОРЕНЬ_ДО_ИМЕНОВАНИЯ as прежний
    названный = charoite_paths.use_data_root(tmp_path / "новый-корень", replace=True)
    assert прежний is not None and названный != прежний, (
        "предпосылка: корень, который канон отвечал ДО называния, известен и отличается")
    for имя in МОДУЛИ_БЕЗ_СНИМКА:
        модуль = sys.modules.get(имя)        # взят уже импортированным, а не импортируется здесь
        assert модуль is not None, f"{имя} не импортирован обвязкой — свидетель бессилен"
        if имя in КАК_СПРОСИТЬ:
            спросить = КАК_СПРОСИТЬ[имя]
            if спросить is None:
                continue                 # у `llm` своя проверка ниже, на живом объекте
        else:
            # Аккессор может жить методом класса (так у `llm`) — тогда модуль
            # спросить нечем, и без этой ветки свидетель падал бы безымянным
            # `AttributeError` на честном коде вместо названного пропуска
            # (круг 5 по коду №329, DS I5 = GLM Minor 6).
            assert hasattr(модуль, "_root"), (
                f"{имя} попал в реестр, но модульного `_root()` у него нет — "
                f"вписать способ вопроса в КАК_СПРОСИТЬ (tests/conftest.py)")
            спросить = lambda м: м._root()   # noqa: E731 — одно выражение, читается на месте
        ответ = спросить(модуль)
        assert ответ == названный, f"{имя} держит замороженный корень: {ответ}"
    # снимок мог переехать в объект: `self._ROOT = resolve_root(...)` в
    # `__init__` ответил бы верно, если объект создан после называния корня —
    # поэтому ищем не момент, а СЛЕД: путь, осевший в атрибуте (круг 2 по
    # коду №329, GLM I3)
    from conftest import ДВИЖОК_ДО_ИМЕНОВАНИЯ as движок
    from conftest import ОШИБКА_ДВИЖКА
    assert движок is not None, (
        f"обвязка не создала движок до называния корня: {ОШИБКА_ДВИЖКА}")
    # след ищем по ЗНАЧЕНИЮ, а не по типу: `pathlib.Path` в атрибуте сам по себе
    # законен (кэш каталога модели, путь конфига), незаконен ПРЕЖНИЙ корень —
    # тот, что был до переименования (круг 3 по коду №329, DS I6 = GLM I3)
    # объект создан ДО называния корня: если снимок осел в `__init__` и его
    # читает `_root()`, аренда уедет в прежний каталог — ловим это ответом, а
    # не формой (круг 3 по коду №329, GLM I3)
    assert движок.lease_dir().parent.parent == названный, (
        f"движок, созданный до называния корня, держит прежний: "
        f"{движок.lease_dir().parent.parent}")
    следы = _следы(vars(движок), прежний) | _следы(vars(type(движок)), прежний)
    assert not следы, f"прежний корень осел в LLM и переименование его не догнало: {следы}"

def test_прогон_переживает_переменную_корня_в_окружении(tmp_path, request):
    """`CHAROITE_ROOT` в шелле не должен ронять сборку всего прогона.

    Переменную штатно экспортируют `scripts/nightly.sh`, приложение детям
    демона и runbook `docs/DATA_AND_RECOVERY.md`, так что запуск тестов в
    таком шелле — обычное дело. Пока публикация корня сессии стояла
    оператором уровня модуля, канон сравнивал её с корнем из окружения и
    отказывал вторым корнем: ошибка сборки на все тесты сразу, ни одного
    зелёного (круг 20 по коду №327, DS I1 = GLM C1).

    Проверяется подпроцессом и только СБОРКОЙ: предмет — то, что conftest
    вообще импортируется, а не поведение отдельного теста.
    """
    # Потолок ребёнка — ПОЛОВИНА потолка теста, и он выводится из конфига, а
    # не захардкожен: иначе первым сработал бы внешний, тест умер бы как
    # «джоб убит по таймауту», и вывод ребёнка не напечатался бы никогда
    # (круг 21, DS I1; круг 22, GLM M3 — отношение должно держаться
    # механизмом, а не комментарием).
    потолок = float(request.config.getini("timeout")) / 2
    окружение = dict(os.environ, CHAROITE_ROOT=str(tmp_path / "чужой"))
    аргументы = [sys.executable, "-m", "pytest", "--collect-only", "-q",
                 "-p", "no:cacheprovider", str(ROOT / "tests" / "test_config_loader.py")]
    try:
        r = subprocess.run(аргументы, capture_output=True, text=True,
                           timeout=потолок, cwd=ROOT, env=окружение)
    except subprocess.TimeoutExpired as повис:
        # Вывод ребёнка живёт атрибутами исключения, и стандартный traceback
        # его не печатает — доносим сами (круг 22 по коду №327, GLM M2).
        pytest.fail(f"сборка с корнем в окружении не кончилась за {потолок} с:\n"
                    f"{повис.stdout}{повис.stderr}")
    # Код возврата накрывает и ошибку сборки (2), и ошибку конфигурации (4),
    # и пустую коллекцию (5) — отдельного утверждения про слово «error» в
    # stdout не нужно, оно краснело бы от имени теста (круг 21, DS M3).
    assert r.returncode == 0, f"сборка упала с корнем в окружении:\n{r.stdout}{r.stderr}"


def test_реестр_модулей_без_снимка_полон():
    """Кто спрашивает корень — знает гейт; реестр обвязки обязан с ним сходиться.

    Второй список — это способ потерять модуль молча: следующий кусок №329
    переводит скрипты, и новый спрашивающий корня просто не попал бы под
    свидетеля (круг 2 по коду №329, GLM I4). Источник истины один —
    `layout_map`, который и так разбирает все файлы дерева.
    """
    import subprocess
    import sys as _sys

    from conftest import МОДУЛИ_БЕЗ_СНИМКА

    корень = pathlib.Path(__file__).resolve().parent.parent
    # Реестр — про модули, у которых есть СВОЙ аккессор корня (`_root`/`_dir`):
    # именно их свидетель и умеет спросить. Модуль, зовущий канон прямо в
    # рабочей функции, аккессора не имеет и проверяется своими тестами.
    # Аккессор — тот, кого свидетель умеет ПОЗВАТЬ: объявленный самим модулем
    # или классом модуля. Вложенная помощница с тем же именем никому не видна
    # снаружи, а `ast.walk` считал и её — модуль попадал в реестр, и свидетель
    # падал на нём `AttributeError` при честном коде (круг 5, GLM Minor 6).
    код = (
        "import ast,pathlib\n"
        "есть=[]\n"
        "for p in sorted(pathlib.Path('src').rglob('*.py')):\n"
        "    t=ast.parse(p.read_text(encoding='utf-8'))\n"
        "    свои=list(t.body)+[m for c in t.body if isinstance(c,ast.ClassDef) for m in c.body]\n"
        "    if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in ('_root','_dir')\n"
        "           for n in свои): есть.append(p.stem)\n"
        "print(' '.join(есть))"
    )
    out = subprocess.run([_sys.executable, "-c", код], cwd=корень,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    с_аккессором = set(out.stdout.split())
    assert с_аккессором, "предпосылка: аккессоры корня в дереве есть"
    пропущены = с_аккессором - set(МОДУЛИ_БЕЗ_СНИМКА)
    assert not пропущены, (
        f"у модулей есть свой аккессор корня, но обвязка их не импортирует и "
        f"свидетель не проверяет: {sorted(пропущены)} — вписать в МОДУЛИ_БЕЗ_СНИМКА")
    # и наоборот: имя в реестре без аккессора — мёртвая запись. Исключения нет:
    # методы классов перечисление теперь видит, и `llm._root` попадает сюда
    # наравне с модульными (круг 6 по коду №329, DS M5 — прежнее `- {"llm"}`
    # стало пустой операцией, а комментарий под ним — неверным)
    лишние = set(МОДУЛИ_БЕЗ_СНИМКА) - с_аккессором
    assert not лишние, f"в реестре имена без аккессора корня: {sorted(лишние)}"
