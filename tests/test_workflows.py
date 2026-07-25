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


# ─── release-app: сборка обязана собирать ТЕГ и не собирать зря ──────────────
#
# Это не гипотеза, а случившийся сценарий: v0.19.0 вышел без бандла, а после
# мёрджа фикса сборка СВЕЖЕГО main прикрепилась к СТАРОМУ тегу v0.19.0 —
# пользователь скачивает «0.19.0», получая код новее релиза. Плюс полная
# macOS-сборка запускалась цепочкой workflow_run после КАЖДОГО пуша в main,
# потому что проверка «ассет уже есть» стояла последним шагом, после сборки.


def _release_steps() -> list[dict]:
    return _load("release-app.yml")["jobs"]["build-app"]["steps"]


def _step_index(steps: list[dict], needle: str) -> int | None:
    """Первый шаг, в run/uses которого встречается подстрока."""
    for i, step in enumerate(steps):
        hay = str(step.get("run", "")) + " " + str(step.get("uses", ""))
        if needle in hay:
            return i
    return None


def test_release_build_waits_for_successful_release_please():
    """workflow_run: types: [completed] приходит и после УПАВШЕГО release-please."""
    cond = str(_load("release-app.yml")["jobs"]["build-app"].get("if", ""))
    assert "conclusion" in cond and "success" in cond, (
        "джоба build-app не смотрит на conclusion запустившего workflow — "
        "сборка стартует даже после упавшего release-please")


def test_tag_is_resolved_before_anything_is_built():
    """Сначала «какому релизу и надо ли», потом сборка — не наоборот."""
    steps = _release_steps()
    resolve = _step_index(steps, "gh release list")
    build = _step_index(steps, "make_app.sh")
    assert resolve is not None and build is not None, "шаги переименованы?"
    assert resolve < build, (
        "тег и наличие ассета выясняются ПОСЛЕ сборки: macOS-раннер собирает "
        "приложение на каждый пуш в main, чтобы в конце сказать «уже есть»")


def test_build_is_skipped_when_release_already_has_asset():
    """Сборка условна: нет релиза без ассета — нет и сборки."""
    steps = _release_steps()
    build = steps[_step_index(steps, "make_app.sh")]
    cond = str(build.get("if", ""))
    assert "steps." in cond and "outputs" in cond, (
        "шаг сборки безусловный — он не спрашивает решение шага-резолвера")


def test_checkout_builds_the_release_tag_not_main():
    """Ассет релиза собирается из кода этого релиза.

    Ровно здесь родился двойник v0.19.0: checkout без ref берёт main,
    make_app.sh штампует версию из git describe, и к тегу приклеивается
    бандл из будущего.
    """
    steps = _release_steps()
    checkouts = [s for s in steps if "actions/checkout" in str(s.get("uses", ""))]
    assert checkouts, "нет шага checkout"
    refs = [str((s.get("with") or {}).get("ref", "")) for s in checkouts]
    assert any("tags/" in r for r in refs), (
        "checkout без ref собирает вершину main — к тегу прикрепится не тот код")


def test_release_app_can_be_pointed_at_a_tag_by_hand():
    """Дозаливка руками: v0.19.0 надо перезалить с его собственного тега."""
    dispatch = _on(_load("release-app.yml")).get("workflow_dispatch") or {}
    tag = (dispatch.get("inputs") or {}).get("tag") or {}
    assert tag.get("required") is True, (
        "нет workflow_dispatch с обязательным inputs.tag — кривой ассет "
        "старого релиза нечем перезалить, кроме как со своей машины")
