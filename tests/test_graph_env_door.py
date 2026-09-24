"""Дверь окружения приложения для слоя графа — graphs.open_search и graphs.revise_cores (№365).

Модули графа окружения не знают: каталог кэша векторов и ночное окно ревизии ядер им
передают. Пока окружение собирало каждое место вызова, пять копий пути кэша и две копии
окна держались без единого теста, а дневной путь ревизии не проверял никто: подмена окна
на «всегда да» или его потеря проходили полный pytest (Opus C1 и I1 круга 1 по коду).
Здесь дверь закреплена один раз, а обход двери ловит сторож раскладки (ENV_SEAMS).
"""
import inspect
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import graph_search  # noqa: E402
import graphs  # noqa: E402
import live_gate  # noqa: E402
import tier3  # noqa: E402


def test_open_search_puts_the_vector_cache_into_the_data_root(tmp_path, monkeypatch):
    # сверка с физическим каталогом, а не со значением той же функции: смена тела
    # search_cache_dir осиротила бы кэш владельца, и граф перевекторизовался бы целиком
    seen = {}

    class Recorder:
        def __init__(self, graph, *, embedder, data_dir, **kw):
            seen.update(graph=graph, embedder=embedder, data_dir=data_dir)

    monkeypatch.setattr(graph_search, "GraphSearch", Recorder)
    graphs.open_search(tmp_path / "граф", "векторизатор")
    assert seen == {"graph": tmp_path / "граф", "embedder": "векторизатор",
                    "data_dir": tmp_path / "данные" / "data"}


def _spy_revise(monkeypatch, report=None, order=None):
    """Подмена revise, которая принимает только вызов, подходящий настоящей сигнатуре,
    и сама спрашивает окно — как спросил бы цикл пар."""
    real = inspect.signature(tier3.revise)
    calls = []

    def spy(*a, **kw):
        real.bind(*a, **kw)
        if order is not None:
            order.append("ревизия")
        calls.append({**kw, "окно": kw["may_continue"]()})
        return report or {"log": [], "skipped": [], "pending_merges": []}

    monkeypatch.setattr(tier3, "revise", spy)
    return calls


def _window(monkeypatch, answer=False):
    asked = []

    def night_window_open(root, *, what, **kw):
        asked.append((root, what))
        return answer

    monkeypatch.setattr(live_gate, "night_window_open", night_window_open)
    return asked


def test_revise_cores_carries_the_night_window_of_the_app(tmp_path, monkeypatch):
    asked = _window(monkeypatch, answer=False)
    calls = _spy_revise(monkeypatch)
    graphs.revise_cores(tmp_path / "граф", only_names=["Релиз"], embedder=object(), judge=object())
    assert asked == [(tmp_path / "данные", "ревизия ядер")]
    assert [c["окно"] for c in calls] == [False]


def test_revise_cores_does_not_take_a_window_from_the_caller(tmp_path, monkeypatch):
    _spy_revise(monkeypatch)
    with pytest.raises(TypeError):
        graphs.revise_cores(tmp_path, may_continue=lambda: True, embedder=object(), judge=object())


def _day_path(tmp_path, monkeypatch, enabled=True):
    import graph_updater
    graph = tmp_path / "граф"
    (graph / "Ядра").mkdir(parents=True)
    order: list[str] = []
    monkeypatch.setattr(graph_updater.install_profile, "tier3_enabled", lambda cfg: enabled)
    monkeypatch.setattr(graph_updater, "_yield_to_live", lambda: order.append("уступка"))
    monkeypatch.setattr(graph_updater.llm, "embedder", lambda cfg, **kw: object())
    monkeypatch.setattr(graph_updater.nli, "judge", lambda: object())
    return graph_updater, graph, order


# Слияние ядер необратимо: право даёт только строгое `tier3_auto_apply: true`, строка «false»
# — не согласие. Прежде это держал тест по тексту исходника, и мутант `apply=True` проходил
# весь набор (Opus I1 круга 2 по №365)
@pytest.mark.parametrize("cfg, apply", [
    ({}, False),
    ({"sufler": {"tier3_auto_apply": "false"}}, False),
    ({"sufler": {"tier3_auto_apply": True}}, True),
])
def test_the_day_path_revises_the_meeting_cores_through_the_door(tmp_path, monkeypatch, capsys, cfg, apply):
    # дневной путь (разбор встречи) — тот, которого не проверял ни один тест; его
    # except Exception превращал потерянный параметр в строку «tier3: пропущен»
    graph_updater, graph, order = _day_path(tmp_path, monkeypatch)
    asked = _window(monkeypatch, answer=False)
    calls = _spy_revise(monkeypatch, order=order)
    graph_updater._revise_meeting_cores(cfg, graph, [{"имя": "Релиз"}])
    assert "tier3: пропущен" not in capsys.readouterr().out
    assert [(c["only_names"], c["mark"], c["apply"]) for c in calls] == [(["Релиз"], True, apply)]
    assert asked == [(tmp_path / "данные", "ревизия ядер")]
    assert order == ["уступка", "ревизия"], "ревизия тянет эмбеддер — сначала уступить живой встрече"


def test_the_day_path_is_silent_when_the_profile_turns_the_revision_off(tmp_path, monkeypatch, capsys):
    graph_updater, graph, order = _day_path(tmp_path, monkeypatch, enabled=False)
    calls = _spy_revise(monkeypatch, order=order)
    graph_updater._revise_meeting_cores({}, graph, [{"имя": "Релиз"}])
    assert calls == [] and order == []
    assert "выключена профилем" in capsys.readouterr().out


def test_bench_search_goes_through_the_door_with_its_own_config(tmp_path, monkeypatch):
    # бенч меряет боевой контур: индекс — через дверь, векторизатор — из переданного конфига,
    # без конфига — пустой (мутант «cfg and {}» выживал, мутатор по всему диапазону №365)
    sys.path.insert(0, str(REPO / "scripts"))
    import memory_bench as mb
    seen, opened = [], []
    monkeypatch.setattr(mb, "build_embedder", lambda cfg: seen.append(cfg) or "векторизатор")

    class Found:
        empty, fragments = True, ""

    class Index:
        def refresh(self, force=False):
            pass

        def load_vectors(self):
            pass

        def search(self, query, limit, snippet_chars):
            return Found()

    monkeypatch.setattr(mb.graphs, "open_search", lambda graph, emb: opened.append(emb) or Index())
    cfg = {"llm": {"embed_model": "bge-m3"}}
    assert mb.search(tmp_path / "первый", "запрос", cfg) == ""
    assert mb.search(tmp_path / "второй", "запрос") == ""
    assert seen == [cfg, {}] and opened == ["векторизатор", "векторизатор"]
