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
import re

import yaml

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
APP = WF.parent.parent / "app"


def _load(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML читает голый ключ `on:` как булево True (YAML 1.1)
    return wf.get("on") or wf.get(True) or {}


def test_ci_runs_on_every_push():
    """Пуш в ветку обязан гонять pytest, не дожидаясь PR."""
    on = _on(_load("ci.yml"))
    # `push:` без значения парсится в None — поэтому сначала «ключ есть»,
    # иначе тест зелёный и при полностью удалённом push-триггере
    assert "push" in on, "ci.yml: push-триггер удалён — пуш не гоняет ничего"
    assert not (on["push"] or {}).get("branches"), (
        "ci.yml: push отфильтрован до main — ветка без PR не даёт сигнала, "
        "и красный тест виден только после того, как работа уже оформлена")


def test_swift_tests_run_on_every_push_touching_app():
    """То же для Swift: пуш ветки, трогающей Swift-код, собирает и гоняет тесты.

    Фильтр paths остаётся (незачем гонять macos-раннер на пуш, не трогающий
    приложение), но он обязан покрывать ОБА Swift-таргета. Пока в нём стоял
    один app/**, iPhone-компаньон не собирался в CI ни разу: правка в app-ios/
    уезжала в main непроверенной.
    """
    wf = _load("swift-tests.yml")
    push = _on(wf).get("push") or {}
    assert not push.get("branches"), (
        "swift-tests.yml: push отфильтрован до main — Swift-правка в ветке "
        "не компилируется, пока нет PR")
    paths = set(push.get("paths") or [])
    assert {"app/**", "app-ios/**"} <= paths, (
        f"paths={sorted(paths)} — не покрыт один из Swift-таргетов, "
        f"его правки поедут в main без единой проверки")
    assert any("app-ios" in str(job) for job in wf["jobs"].values()), \
        "нет джобы, собирающей app-ios — фильтр paths его пускает, а собирать некому"


def test_swift_live_probes_compile_but_do_not_run_in_gates():
    """Пробы с реальным графом/Ollama не являются зелёными CI-тестами.

    Они живут в отдельном target и обязаны компилироваться. PR и nightly
    исполняют только deterministic suite: иначе отсутствие приватных данных
    снова превратится в пять успешных skip и создаст ложную галочку.
    """
    package = (APP / "Package.swift").read_text(encoding="utf-8")
    assert 'name: "CharoiteAppLiveProbes"' in package
    assert 'path: "Probes"' in package

    probes = {
        "AnswerQualityProbe.swift",
        "BuildRealIndex.swift",
        "LiveAnswerProbe.swift",
        "MemoryBench.swift",
        "SearchPerfProbe.swift",
    }
    actual = {path.name for path in (APP / "Probes").glob("*.swift")}
    assert actual == probes, (
        "набор файлов в Probes/ разошёлся со списком стража: "
        f"лишние {sorted(actual - probes)}, пропавшие {sorted(probes - actual)} — "
        "новую пробу добавь и сюда, и в doc-команды запуска")
    assert not any((APP / "Tests" / name).exists() for name in probes)
    deterministic_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (APP / "Tests").rglob("*.swift"))
    # Живая проба под любым именем гейтится приватным окружением — сам
    # env-гейт и запрещён в детерминированных исходниках (круг-1: DS и GLM
    # сошлись: реестр имён обходится переименованием). Сегодня в Tests/
    # таких чтений ноль, ложных срабатываний нет.
    assert 'environment["CHAROITE_' not in deterministic_sources, \
        "env-гейченая живая проба вернулась в детерминированный target"
    for live_case in (
        "AnswerQualityProbe", "BuildRealIndex", "LiveAnswerProbe",
        "MemoryBench", "SearchPerfProbe",
    ):
        assert f"class {live_case}" not in deterministic_sources, \
            f"{live_case}: живая проба вернулась в основной test target"

    workflows = {
        "swift-tests.yml": _load("swift-tests.yml")["jobs"]["test"],
        "nightly.yml": _load("nightly.yml")["jobs"]["swift"],
    }
    for name, job in workflows.items():
        commands = [str(step.get("run", "")) for step in job["steps"]]
        assert any("--build-tests" in command for command in commands), \
            f"{name}: живые пробы больше не компилируются"
        test_commands = [command for command in commands if "swift test" in command]
        assert len(test_commands) == 1
        assert "--filter" in test_commands[0] and "CharoiteAppTests" in test_commands[0], \
            f"{name}: CI снова запускает живые пробы как обычные тесты"
        assert "CharoiteAppLiveProbes" not in test_commands[0]
        # Сцепка фильтра с манифестом: фильтр, чей префикс не совпадает с
        # именем существующего testTarget, отбирает ноль тестов и выходит
        # нулём — «зелёный без прогона» (круг-1: DS+GLM). Grep по Executed
        # в самих workflow — второй слой той же защиты.
        m = re.search(r"--filter '\^([A-Za-z0-9_]+)\\\.'", test_commands[0])
        assert m, f"{name}: фильтр не в каноничной форме ^Таргет\\."
        assert f'name: "{m.group(1)}"' in (APP / "Package.swift").read_text(encoding="utf-8"), \
            f"{name}: фильтр указывает на несуществующий testTarget {m.group(1)}"


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


def test_concurrency_key_separates_pull_requests_from_different_forks():
    """head_ref — только ИМЯ ветки, без владельца форка.

    Два PR из разных форков с веткой main (типовой drive-by) дают одну
    группу; cancel-in-progress: true — пуш во второй PR отменяет идущий
    чек первого, и обязательная проверка виснет красной. Ключом события
    pull_request обязан быть номер PR — он уникален в репозитории.
    """
    for name in ("ci.yml", "swift-tests.yml"):
        group = str((_load(name).get("concurrency") or {}).get("group", ""))
        assert "pull_request.number" in group, (
            f"{name}: группа ключуется по head_ref — PR из разных форков "
            "с одинаковым именем ветки отменяют чеки друг друга")


def test_android_changes_run_the_full_gradle_gate():
    """Android-код нельзя мёрджить, ни разу не собрав Kotlin."""
    wf = _load("android-tests.yml")
    on = _on(wf)
    for event in ("push", "pull_request"):
        paths = set((on.get(event) or {}).get("paths") or [])
        assert "app-android/**" in paths, (
            f"android-tests.yml: {event} не покрывает app-android/**")

    job = wf["jobs"]["test"]
    assert job.get("timeout-minutes"), "Android-сборка может зависнуть без таймаута"
    steps = job["steps"]
    uses = {str(step.get("uses", "")) for step in steps}
    assert any(use.startswith("actions/setup-java@") for use in uses)
    assert any(use.startswith("android-actions/setup-android@") for use in uses)
    assert any(use.startswith("gradle/actions/setup-gradle@") for use in uses)

    java = next(step for step in steps if "actions/setup-java@" in str(step.get("uses")))
    assert str((java.get("with") or {}).get("java-version")) == "17"
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    for task in ("testDebugUnitTest", "lintDebug", "assembleDebug"):
        assert task in commands, f"Android CI не запускает {task}"


def test_android_workflow_has_safe_concurrency_key():
    """Push и PR одного Android-коммита не должны отменять друг друга."""
    group = str((_load("android-tests.yml").get("concurrency") or {}).get("group", ""))
    assert "event_name" in group
    assert "pull_request.number" in group


def test_docs_guard_treats_android_as_code():
    """Изменение Android-поведения требует документации или skip-docs."""
    source = (WF / "docs-guard.yml").read_text(encoding="utf-8")
    assert "app-android/" in source
    assert r"(app-android|scripts)/README\.md" in source


def test_dependabot_tracks_android_gradle_dependencies():
    """Новые Kotlin/Android-зависимости не должны стареть молча."""
    config = yaml.safe_load(
        (WF.parent / "dependabot.yml").read_text(encoding="utf-8"))
    assert any(
        update.get("package-ecosystem") == "gradle"
        and update.get("directory") == "/app-android"
        for update in config["updates"]
    )


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


def test_tag_resolver_skips_draft_releases():
    """gh release list показывает и драфты — а у драфта git-тега ещё НЕТ.

    Мейнтейнер держит черновик релиз-нот → каждый пуш в main дотягивается
    до resolve, тот берёт драфт-тег, ассета нет → build=true → checkout
    refs/tags/<драфт> падает «couldn't find remote ref» — красный
    release-app на каждом пуше, пока жив черновик.
    """
    steps = _release_steps()
    resolve = steps[_step_index(steps, "gh release list")]
    assert "--exclude-drafts" in str(resolve.get("run", "")), (
        "резолв свежайшего релиза не исключает драфты — черновик релиз-нот "
        "уронит сборку: у драфта есть tagName, но нет git-тега")


def test_release_app_can_be_pointed_at_a_tag_by_hand():
    """Дозаливка руками: v0.19.0 надо перезалить с его собственного тега."""
    dispatch = _on(_load("release-app.yml")).get("workflow_dispatch") or {}
    tag = (dispatch.get("inputs") or {}).get("tag") or {}
    assert tag.get("required") is True, (
        "нет workflow_dispatch с обязательным inputs.tag — кривой ассет "
        "старого релиза нечем перезалить, кроме как со своей машины")
