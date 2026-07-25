"""Контур обратной связи живёт в триггерах workflow — значит, они тоже код.

Диагноз всего захода: пилот чинит только те дефекты, которые дают
механический, немедленный сигнал в его собственном цикле. Коммит, который
включил pytest в CI, расширил контур на пул-реквесты. Но пуш в ветку без
PR по-прежнему не запускает ничего:

    ci.yml           on.push.branches: [main]
    swift-tests.yml  on.push.branches: [main] (+ paths: app/**)

То есть запушенная ветка — 27 коммитов, любых — не производит ни одной
галочки, пока кто-то не откроет PR. Для автономного пилота это дыра в
контуре: сигнал появляется только на последнем шаге, а не в момент пуша.

Тесты структурные, по YAML самих workflow: прогнать GitHub Actions здесь
нельзя, а инвариант всё равно текстовый — «какие события запускают прогон».
"""
import pathlib

import yaml

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML читает голый ключ `on:` как булево True (YAML 1.1)
    return wf.get("on") or wf.get(True) or {}


def test_ci_runs_on_every_push():
    """Пуш в ветку обязан гонять pytest, не дожидаясь PR."""
    push = _on(_load("ci.yml")).get("push")
    assert push is None or not (push or {}).get("branches"), (
        "ci.yml: push отфильтрован до main — ветка без PR не даёт сигнала, "
        "и красный тест виден только после того, как работа уже оформлена")


def test_swift_tests_run_on_every_push_touching_app():
    """То же для Swift: пуш ветки, трогающей app/, собирает и гоняет тесты."""
    push = _on(_load("swift-tests.yml")).get("push") or {}
    assert not push.get("branches"), (
        "swift-tests.yml: push отфильтрован до main — Swift-правка в ветке "
        "не компилируется, пока нет PR")
    assert push.get("paths") == ["app/**"], (
        "фильтр paths: [app/**] должен остаться — незачем гонять macos-раннер "
        "на пуш, не трогающий приложение")


def test_twin_runs_do_not_cancel_each_other():
    """Пуш в ветку с открытым PR порождает ДВА события: push и pull_request.

    Оба запускают workflow. Если concurrency-группа не различает событие,
    второй прогон отменяет первый — а отменённый обязательный чек приходит
    в PR как красный. Группа обязана включать event_name.
    """
    for name in ("ci.yml", "swift-tests.yml"):
        group = str((_load(name).get("concurrency") or {}).get("group", ""))
        assert "event_name" in group, (
            f"{name}: в concurrency.group нет github.event_name — push- и "
            "pull_request-прогоны одного коммита будут отменять друг друга")
