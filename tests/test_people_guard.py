"""Имена людей графа владельца — второй источник стража (№605).

Шесть полных имён из узлов Люди ушли в публичный main и два месяца проходили
pre-commit, pre-push и `--all`: страж знал только статический список (№604).
Имена держит гейт брифа внешних голов (`check_brief.py --people-scan`), страж
зовёт его на машине владельца. Здесь — контракт со стороны стража на заглушке
гейта (идёт и в CI) и сквозной опыт на настоящем гейте с подставным графом
(только там, где гейт есть). Все имена выдуманы.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from test_pre_push_markers import MARKER, REPO, Repo, _owner_env, git, guard_run

FAKE = "Аристарх" + " Чебуреков"            # из частей: целиком сработал бы на этом файле у владельца
LABEL = "Собеседник 3"
# Какой гейт копировать в подставной HOME: по умолчанию — установленный; ветка
# гейта до мержа — через переменную (только выбор копии для теста, страж её не читает).
REAL_GATE = pathlib.Path(os.environ.get("CHAROITE_TEST_PEOPLE_GATE")
                         or pathlib.Path.home() / ".config" / "charoite" / "check_brief.py")


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path)
    for rel in ("src/charoite_graph/__init__.py", "src/charoite_graph/graph_names.py"):
        (r.work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, r.work / rel)
    r.commit("rule of labels")
    r.install()
    return r


def _people(repo: Repo, *names: str) -> None:
    gate_dir = repo.tmp / "home" / ".config" / "charoite"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "people.txt").write_text("\n".join(names), encoding="utf-8")


def _stage(repo: Repo, name: str, text: str) -> None:
    repo.write(name, text)
    git(repo.work, "add", "-A", env=repo.env)


def test_name_from_the_graph_blocks_the_commit_without_quoting_it(repo):
    env = _owner_env(repo)
    _people(repo, FAKE)
    _stage(repo, "docs/note.md", f"начало\nсозвон с {FAKE} в пятницу\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "docs/note.md:2" in p.stderr, p.stderr
    assert "Чебуреков" not in p.stdout + p.stderr, "страж процитировал имя"


def test_name_in_a_file_path_hides_the_path(repo):
    env = _owner_env(repo)
    _people(repo, FAKE)
    _stage(repo, f"docs/{FAKE}.md", f"ничего\nи снова {FAKE}\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "имя файла (путь скрыт)" in p.stderr, p.stderr
    assert "(путь скрыт):2" in p.stderr, "строка в файле с именем в пути — без пути"
    assert "Чебуреков" not in p.stdout + p.stderr


def test_old_name_in_a_touched_file_is_found_too(repo):
    """Pre-commit судит изменённый файл целиком: имя, попавшее в граф после
    публикации строки, всплывает при следующей правке файла."""
    env = _owner_env(repo)
    repo.write("docs/old.md", f"{FAKE}\n")
    repo.commit("old")
    _people(repo, FAKE)
    _stage(repo, "docs/old.md", f"{FAKE}\nновая строка\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "docs/old.md:1" in p.stderr, p.stderr


def test_diarization_label_in_people_is_not_a_person(repo):
    env = _owner_env(repo)
    _people(repo, LABEL)
    _stage(repo, "docs/labels.md", f"метка {LABEL} — не человек\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 0, p.stderr


def test_real_name_with_a_label_in_brackets_is_a_person(repo):
    """«Аристарх (Собеседник 4)» — человек с пометкой модели, не метка: находка
    остаётся (out1 I1 Sonnet, I2 DeepSeek)."""
    env = _owner_env(repo)
    named = "Аристарх (Собеседник 4)"
    _people(repo, named, LABEL)
    _stage(repo, "docs/a.md", f"{named}\n{LABEL}\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "docs/a.md:1" in p.stderr and "docs/a.md:2" not in p.stderr, p.stderr


def test_all_refuses_an_edited_label_rule_when_it_would_drop_a_hit(repo):
    """Правка правила меток в рабочей копии — отказ в любом режиме, где правило
    отбрасывает находку, не только в pre-push (out1 I1 DeepSeek)."""
    env = _owner_env(repo)
    repo.write("docs/old.md", f"{FAKE}\n")
    repo.commit("old")
    _people(repo, FAKE)
    rule = repo.work / "src" / "charoite_graph" / "graph_names.py"
    rule.write_text(rule.read_text(encoding="utf-8") + "\nis_speaker_placeholder = lambda stem: True\n",
                    encoding="utf-8")
    p = subprocess.run([sys.executable, str(repo.work / "scripts" / "check_private_markers.py"), "--all"],
                       cwd=repo.work, env=env, capture_output=True, text=True)    # страж этого checkout
    assert p.returncode == 1 and "правило меток" in p.stderr, p.stderr


def test_synthetic_name_outside_the_graph_passes(repo):
    env = _owner_env(repo)
    _people(repo, FAKE)
    _stage(repo, "docs/ok.md", "Иван Тестовый — пример из документации\n")
    assert guard_run(repo.work, env=env).returncode == 0


@pytest.mark.parametrize("gate", [None, "import sys\nsys.exit(2)\n",
                                  'print(\'{"v": 1, "records": 999, "names": 5, "hits": []}\')\n',
                                  'print(\'{"v": 2, "records": 2, "names": 5, "hits": []}\')\n',
                                  # гейт, который вход не читал, а счёт записей угадал (out1 C5 Haiku)
                                  'import json, sys\nr = json.load(sys.stdin)\nprint(json.dumps({"v": 1, '
                                  '"records": len(r), "names": 5, "digest": "0" * 64, "hits": []}))\n'])
def test_owner_machine_without_a_working_gate_is_refused(repo, gate):
    """Нет гейта, гейт не смог, ответ не сходится с запросом, старая версия —
    отказ, а не «чисто»: имена людей не проверены."""
    env = _owner_env(repo, gate=gate)
    _stage(repo, "docs/a.md", "раз\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "страж не смог проверить" in p.stderr, p.stderr


def test_ci_and_markers_env_do_not_switch_the_owner_source_off(repo):
    """CI=1 и CHAROITE_MARKERS на несуществующий путь на машине владельца не
    дают пропуска (№605 in1 I1)."""
    env = _owner_env(repo, CI="1", CHAROITE_MARKERS=str(repo.tmp / "нет.txt"))
    _people(repo, FAKE)
    _stage(repo, "docs/note.md", f"{FAKE}\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "имена людей графа" in p.stderr, p.stderr


def test_ci_does_not_switch_the_author_check_off_on_the_owner_machine(repo):
    """CI=1 на машине владельца не снимает проверку автора (№605 in1, Haiku I3)."""
    env = _owner_env(repo, CI="1")
    git(repo.work, "config", "user.email", "someone@example.com", env=repo.env)
    _stage(repo, "docs/a.md", "раз\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "Автор коммита" in p.stderr, p.stderr


def test_contributor_without_the_owner_list_needs_no_gate(repo):
    env = dict(repo.env, CI="1")
    _stage(repo, "docs/a.md", "раз\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 0, p.stderr


def test_range_judges_messages_and_lines_split_by_commit(repo):
    env = _owner_env(repo)
    _people(repo, FAKE)
    repo.write("docs/a.md", "чисто\n")
    repo.commit(f"созвон с {FAKE}")
    repo.write("docs/b.md", f"раз\nдва {FAKE}\n")
    sha = repo.commit("b")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=env)
    assert p.returncode == 1, p.stderr
    assert "сообщение: имя человека" in p.stderr and f"{sha[:9]} docs/b.md:2" in p.stderr, p.stderr
    assert "Чебуреков" not in p.stderr


def test_range_hides_a_path_with_a_name_in_its_line_hits_too(repo):
    env = _owner_env(repo)
    _people(repo, FAKE)
    repo.write(f"docs/{FAKE}.md", f"{FAKE}\n")
    repo.commit("c")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=env)
    assert p.returncode == 1 and "имя файла (путь скрыт)" in p.stderr, p.stderr
    assert "(путь скрыт):1" in p.stderr and "Чебуреков" not in p.stderr, p.stderr


def test_all_finds_a_published_name_in_the_tree(repo):
    env = _owner_env(repo)
    repo.write("docs/old.md", f"раз\n{FAKE}\n")
    repo.commit("old")
    _people(repo, FAKE)
    p = guard_run(repo.work, "--all", env=env)
    assert p.returncode == 1 and "docs/old.md:2" in p.stderr, p.stderr


def test_pre_push_refuses_an_edited_label_rule_in_the_main_checkout(repo):
    """Метки страж берёт из `src/` основного checkout: правка правила там
    выключила бы источник мимо хука (in1 I7)."""
    env = _owner_env(repo)
    rule = repo.work / "src" / "charoite_graph" / "graph_names.py"
    rule.write_text(rule.read_text(encoding="utf-8") + "\nis_placeholder_node = lambda stem: True\n",
                    encoding="utf-8")
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    p = git(wt, "push", "origin", "feat", env=env, check=False)
    assert p.returncode != 0 and "правило меток" in p.stderr, p.stderr


def test_pre_push_hides_a_branch_name_with_a_person(repo):
    """Имя ссылки — запись гейта; отказ не печатает его ни строкой имени, ни
    строкой маркера (out1 C1 обеих голов)."""
    env = _owner_env(repo)
    _people(repo, "Аристарх")
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    p = git(wt, "push", "origin", "feat:refs/heads/feat/Аристарх", env=env, check=False)
    assert p.returncode != 0 and "имя ссылки (скрыто)" in p.stderr, p.stderr
    assert "Аристарх" not in p.stdout + p.stderr, p.stderr


def test_pre_commit_hides_a_published_path_that_also_has_a_marker(repo):
    """Опубликованный файл с именем в пути и маркером в тексте: проход маркеров
    по дереву печатает пути — имя людей судится раньше, путь не печатается (out1 C2)."""
    env = _owner_env(repo)
    repo.write(f"docs/{FAKE}.md", f"{MARKER}\n")
    repo.commit("old")
    _people(repo, FAKE)
    _stage(repo, "docs/a.md", "раз\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "имя файла (путь скрыт)" in p.stderr, p.stderr
    assert "Чебуреков" not in p.stdout + p.stderr, p.stderr


def test_range_hides_the_path_in_a_binary_blob_line(repo):
    """Строка «(двоичный)» печатает путь маской маркеров — имя людей в нём
    скрывается той же точкой печати (out1 C2 Sonnet)."""
    env = _owner_env(repo)
    _people(repo, FAKE)
    (repo.work / "docs").mkdir(exist_ok=True)
    (repo.work / "docs" / f"{FAKE}.docx").write_bytes(b"PK\x00\x01\x02")
    repo.commit("bin")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=env)
    assert p.returncode == 1 and "(двоичный)" in p.stderr, p.stderr
    assert "Чебуреков" not in p.stdout + p.stderr, p.stderr


def test_gate_stderr_is_not_printed(repo):
    """Сбой гейта: его stderr (пути графа с именами) страж не печатает."""
    env = _owner_env(repo, gate=f"import sys\nprint('ссылка в графе: Люди/{FAKE}.md', file=sys.stderr)\nsys.exit(2)\n")
    _stage(repo, "docs/a.md", "раз\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "страж не смог проверить" in p.stderr, p.stderr
    assert "Чебуреков" not in p.stdout + p.stderr, p.stderr


def test_pre_push_label_rule_ignores_a_shim_in_the_package_init(repo):
    """`__init__.py` пакета вне сверки канона: подмена правила там не выключает
    имена (out1 C2 DeepSeek) — страж грузит один файл правила."""
    env = _owner_env(repo)
    (repo.work / "src" / "charoite_graph" / "__init__.py").write_text(
        "import sys, types\n_m = types.ModuleType('charoite_graph.graph_names')\n"
        "_m.is_placeholder_node = lambda stem: True\nsys.modules['charoite_graph.graph_names'] = _m\n",
        encoding="utf-8")
    _people(repo, FAKE)
    wt = repo.worktree("feat")
    repo.write("n.md", f"{FAKE}\n", wt)
    repo.commit("leak", wt)
    p = git(wt, "push", "origin", "feat", env=env, check=False)
    assert p.returncode != 0 and "имя человека из графа" in p.stderr, p.stderr


# ── настоящий гейт, подставной граф ──────────────────────────────────────────

def _real_gate_or_skip() -> pathlib.Path:
    """Гейт есть — сквозной опыт. Гейта нет: в CI и у контрибьютора — пропуск; на
    машине владельца (есть список по умолчанию) — провал, а не тихий пропуск."""
    if REAL_GATE.is_file():
        return REAL_GATE
    if (REAL_GATE.parent / "private_markers.txt").exists() and not os.environ.get("CI"):
        pytest.fail(f"машина владельца без гейта имён {REAL_GATE}")
    pytest.skip("гейта имён нет (CI, контрибьютор)")


@pytest.fixture
def graph_repo(repo):
    gate = _real_gate_or_skip()
    env = _owner_env(repo, gate=gate.read_text(encoding="utf-8"))
    people = repo.tmp / "home" / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents" / "Граф" / "Люди"
    people.mkdir(parents=True)
    for i in range(1000):                     # полы гейта: 1000 имён графа, 100 людей
        (people / f"Тестовый Узел {i}.md").write_text("x", encoding="utf-8")
    (people / f"{FAKE}.md").write_text("x", encoding="utf-8")
    (people / f"{LABEL}.md").write_text("x", encoding="utf-8")
    return repo, env


@pytest.mark.parametrize("text", [f"передал {FAKE.split()[0]}у {FAKE.split()[1]}у отчёт\n",
                                  f"{FAKE.split()[1]}\n{FAKE.split()[0]} — автор\n"])
def test_real_gate_finds_case_forms_and_line_breaks(graph_repo, text):
    """Падеж и порядок слов (in1 C1), имя через перенос строки (in1 I4)."""
    repo, env = graph_repo
    _stage(repo, "docs/note.md", text)
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "docs/note.md" in p.stderr, p.stderr
    assert "Чебуреков" not in p.stdout + p.stderr


def test_real_gate_passes_labels_and_other_names(graph_repo):
    repo, env = graph_repo
    _stage(repo, "docs/ok.md", f"{LABEL} сказал; Иван Тестовый — пример\n")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 0, p.stderr


def test_real_gate_refuses_an_unloaded_people_node(graph_repo):
    """Узел Люди выгружен iCloud (`.Имя.md.icloud`): имени в обходе нет — отказ (in1 I5)."""
    repo, env = graph_repo
    _stage(repo, "docs/a.md", "раз\n")
    assert guard_run(repo.work, env=env).returncode == 0         # контроль: гейт отвечает
    people = next((repo.tmp / "home" / "Library").rglob("Люди"))
    (people / f"{FAKE}.md").rename(people / f".{FAKE}.md.icloud")
    p = guard_run(repo.work, env=env)
    assert p.returncode == 1 and "страж не смог проверить" in p.stderr, p.stderr
    gate = repo.tmp / "home" / ".config" / "charoite" / "check_brief.py"
    g = subprocess.run([sys.executable, str(gate), "--people-scan"], input="[]", env=env,
                       capture_output=True, text=True)
    assert g.returncode == 2 and "выгружены iCloud" in g.stderr, g.stderr
