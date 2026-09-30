"""Страж по каждому уходящему коммиту (№541).

Исполнители пушат ветку после каждого коммита. Промежуточный коммит публичного
репозитория опубликован навсегда, даже если следующий его исправил: маркер в
коммите 1, убранный в коммите 2, итоговый дифф не показывает, а GitHub хранит.
Поэтому страж судит каждый коммит набора — строки, имена файлов, сообщение,
почту — а хук pre-push зовёт копию стража из основного checkout на main, чтобы
ветка не могла ослабить гейт, через который публикуется.

Всё — на настоящих временных репозиториях с «удалённым» bare и настоящим
`git push`: страж живёт на стыке с git, подделка git здесь проверяла бы подделку.
Список маркеров — подставной (`CHAROITE_MARKERS`), как в test_private_markers.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_private_markers.py"
sys.path.insert(0, str(REPO / "scripts"))

import check_private_markers as guard  # noqa: E402

MARKER = "ВнутренняяСистемаЫЪ"
# Личный путь собирается из частей: целиком он сработал бы на самом этом файле.
LEAK_PATH = "/Us" + "ers/u/work/notes.txt"
SURNAME_INITIALS = "Иванов " + "И. И."
OWNER = guard.MAINTAINER_EMAIL


def _env(tmp: pathlib.Path, markers: bool = True, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "PRE_COMMIT_")) and k not in ("CI", "CHAROITE_MARKERS")}
    env["HOME"] = str(tmp / "home")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if markers:
        env["CHAROITE_MARKERS"] = str(tmp / "markers.txt")
    env.update(extra)
    return env


def git(cwd: pathlib.Path, *args: str, env: dict[str, str], check: bool = True):
    p = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check:
        assert p.returncode == 0, f"git {args}: {p.stdout}{p.stderr}"
    return p


def guard_run(cwd: pathlib.Path, *args: str, env: dict[str, str], stdin: str = ""):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, env=env,
                          input=stdin, capture_output=True, text=True)


class Repo:
    """Основной checkout на main + bare-«удалённый»; хуков нет, пока их не поставят."""

    def __init__(self, tmp: pathlib.Path):
        self.tmp = tmp
        (tmp / "home").mkdir()
        (tmp / "markers.txt").write_text(f"# комментарий — не маркер\n{MARKER}\n", encoding="utf-8")
        self.env = _env(tmp)
        self.remote = tmp / "remote.git"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(self.remote), env=self.env)
        self.work = tmp / "work"
        git(tmp, "init", "-q", "-b", "main", str(self.work), env=self.env)
        for k, v in (("user.email", OWNER), ("user.name", "Charoite AI")):
            git(self.work, "config", k, v, env=self.env)
        (self.work / "scripts").mkdir()
        shutil.copy(SCRIPT, self.work / "scripts" / "check_private_markers.py")
        self.write("README.md", "начало\n")
        self.commit("init")
        git(self.work, "remote", "add", "origin", str(self.remote), env=self.env)
        git(self.work, "push", "-q", "origin", "main", env=self.env)
        self.base = self.head()

    def write(self, name: str, text: str, where: pathlib.Path | None = None) -> None:
        path = (where or self.work) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, msg: str, where: pathlib.Path | None = None, *extra: str) -> str:
        """Коммит мимо pre-commit: так выглядит клон без хука или маркер, внесённый
        в список позже, — ровно то, что обязан поймать второй рубеж на push."""
        cwd = where or self.work
        git(cwd, "add", "-A", env=self.env)
        git(cwd, "-c", f"core.hooksPath={self.tmp / 'no-hooks'}", "commit", "-q",
            "-m", msg, *extra, env=self.env)
        return self.head(cwd)

    def head(self, cwd: pathlib.Path | None = None) -> str:
        return git(cwd or self.work, "rev-parse", "HEAD", env=self.env).stdout.strip()

    def install(self) -> None:
        p = guard_run(self.work, "--install-hooks", env=self.env)
        assert p.returncode == 0, p.stderr

    def worktree(self, branch: str) -> pathlib.Path:
        wt = self.tmp / f"wt-{branch}"
        git(self.work, "worktree", "add", "-q", "-b", branch, str(wt), env=self.env)
        return wt


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


# ── покоммитный разбор ──────────────────────────────────────────────────────

def test_marker_removed_in_a_later_commit_still_blocks(repo):
    """Главный случай задачи: итоговый дифф чист, промежуточный коммит — нет.
    Опровергающий опыт — суд по итоговому диффу обязан сделать этот тест красным."""
    repo.write("notes.md", f"строка\nзапуск на {MARKER}\n")
    bad = repo.commit("x")
    repo.write("notes.md", "строка\nзапуск на стенде\n")
    repo.commit("убрал")
    assert git(repo.work, "diff", f"{repo.base}..HEAD", env=repo.env).stdout.count(MARKER) == 0
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1, p.stdout + p.stderr
    assert f"{bad[:9]} notes.md:2: приватный маркер" in p.stderr, p.stderr
    assert MARKER not in p.stderr + p.stdout, "страж процитировал маркер"


def test_public_format_in_an_intermediate_commit_blocks(repo):
    repo.write("cfg.txt", f"path = {LEAK_PATH}\n")
    bad = repo.commit("cfg")
    repo.write("cfg.txt", "path = ~/notes.txt\n")
    repo.commit("fix")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1
    assert f"{bad[:9]} cfg.txt:1: личный путь" in p.stderr, p.stderr


def test_clean_range_passes_and_counts_commits(repo):
    repo.write("a.md", "раз\n")
    repo.commit("a")
    repo.write("b.md", "два\n")
    repo.commit("b")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr
    assert "коммитов проверено: 2, оба набора: чисто" in p.stdout


def test_commit_message_and_author_are_published_too(repo):
    repo.write("a.md", "раз\n")
    msg_sha = repo.commit(f"fix: запуск на {MARKER}")
    repo.write("b.md", "два\n")
    who_sha = repo.commit("b", None, "--author", SURNAME_INITIALS + " <x@example.com>")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1
    assert f"{msg_sha[:9]} сообщение: приватный маркер" in p.stderr, p.stderr
    assert f"{who_sha[:9]} автор: фамилия с инициалами" in p.stderr, p.stderr


def test_file_names_of_binary_and_empty_files_are_checked(repo):
    """У двоичного и пустого файла в патче нет `+++` — имя берётся из --raw."""
    (repo.work / f"{MARKER}.png").write_bytes(b"\x89PNG\r\n\x00\x01")
    (repo.work / "docs").mkdir()
    (repo.work / "docs" / f"отчёт {MARKER}.md").write_text("", encoding="utf-8")
    sha = repo.commit("files")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1
    assert p.stderr.count(f"{sha[:9]} имя файла") == 2, p.stderr


def test_evil_merge_line_is_caught_but_parent_lines_are_not_repeated(repo):
    """Строка, которой нет ни в одном родителе, — новая; строки родителей судятся
    их коммитами. `+++ b/…` внутри хунка — содержимое, не заголовок."""
    git(repo.work, "checkout", "-q", "-b", "side", env=repo.env)
    repo.write("side.md", "с ветки\n")
    repo.commit("side")
    git(repo.work, "checkout", "-q", "main", env=repo.env)
    repo.write("main.md", "с main\n+++ b/выдуманный\n")
    repo.commit("main")
    git(repo.work, "merge", "-q", "--no-commit", "side", env=repo.env)
    repo.write("main.md", f"с main\n+++ b/выдуманный\nзлое {MARKER}\n")
    merge = repo.commit("merge")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1
    lines = [ln.strip() for ln in p.stderr.splitlines() if "приватный маркер" in ln]
    assert lines == [f"{merge[:9]} main.md:3: приватный маркер"], p.stderr


def test_range_from_a_subdirectory_still_sees_the_whole_commit(repo):
    """Pathspec стража — от корня репозитория, не от текущего каталога."""
    repo.write("docs/x.md", "чисто\n")
    repo.write("top.md", f"{MARKER}\n")
    repo.commit("sub")
    p = guard_run(repo.work / "docs", "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and "top.md:1: приватный маркер" in p.stderr, p.stdout + p.stderr


def test_side_branch_hidden_behind_a_treesame_merge_is_still_judged(repo):
    """Ветка с маркером, убранным следом, влита слиянием, равным первому родителю:
    pathspec без --full-history пропускал бы её коммиты (выход 1 №541, Sonnet C1)."""
    git(repo.work, "checkout", "-q", "-b", "side", env=repo.env)
    repo.write("leak.md", f"{MARKER}\n")
    bad = repo.commit("leak")
    (repo.work / "leak.md").unlink()
    repo.commit("убрал")
    git(repo.work, "checkout", "-q", "main", env=repo.env)
    repo.write("feat.md", "своё\n")
    repo.commit("feat")
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "merge", "-q", "--no-ff",
        "-m", "merge side", "side", env=repo.env)
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and f"{bad[:9]} leak.md:1: приватный маркер" in p.stderr, \
        p.stdout + p.stderr


def test_odd_file_names_and_a_last_line_without_newline(repo):
    """Кавычки, пробел, перевод строки в имени — git берёт путь в C-кавычки;
    последняя строка без перевода строки — хвост `\\ No newline`."""
    names = ['кав"ычка.md', "с пробелом.md", "пере\nнос.md"]
    for n in names:
        (repo.work / n).write_text(f"x\n{MARKER}", encoding="utf-8")
    (repo.work / "СНИМОК.PNG").write_text(f"{MARKER}\n", encoding="utf-8")
    repo.commit("odd")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1
    for n in names:
        assert f"{n}:2: приватный маркер" in p.stderr, (n, p.stderr)
    assert "СНИМОК.PNG:" not in p.stderr, "содержимое медиа-суффикса читать не должен"


def test_allow_mark_silences_only_its_own_message_line(repo):
    repo.write("a.md", "раз\n")
    sha = repo.commit(f"пример {guard.PUBLIC_ALLOW}\n\nпуть {LEAK_PATH}")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and f"{sha[:9]} сообщение: личный путь" in p.stderr, p.stderr


def test_git_error_is_a_refusal_not_a_clean_pass(repo):
    p = guard_run(repo.work, "--range", "deadbeef..HEAD", env=repo.env)
    assert p.returncode == 1
    assert "не смог проверить" in p.stderr


@pytest.mark.parametrize("ci", ["", "1"])
def test_range_without_marker_list_refuses_even_with_ci_set(repo, tmp_path, ci):
    """Режимы владельца не читают CI: переменная из окружения сессии не должна
    превращать гейт в «только публичные форматы»."""
    env = _env(tmp_path, CHAROITE_MARKERS=str(tmp_path / "нет-списка.txt"),
               **({"CI": ci} if ci else {}))
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=env)
    assert p.returncode == 1 and "fail-closed" in p.stderr


def test_comment_lines_in_the_list_are_not_markers(repo):
    repo.write("a.md", "# комментарий — не маркер\n")
    repo.commit("a")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr


# ── pre-push владельца ──────────────────────────────────────────────────────

def test_push_of_a_branch_with_a_marker_in_an_intermediate_commit_is_refused(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", f"{MARKER}\n", wt)
    repo.commit("bad", wt)
    repo.write("n.md", "чисто\n", wt)
    repo.commit("fix", wt)
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0, p.stderr
    assert "PUSH ЗАБЛОКИРОВАН" in p.stderr
    assert git(repo.work, "ls-remote", "origin", "feat", env=repo.env).stdout == ""


def test_clean_branch_pushes_and_deletion_is_not_judged(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode == 0, p.stderr
    assert "коммитов проверено: 1" in p.stdout, p.stdout   # вывод хука git отдаёт в stdout
    p = git(wt, "push", "origin", ":feat", env=repo.env, check=False)
    assert p.returncode == 0, p.stderr
    assert "нечего проверять" in p.stdout


def test_branch_cannot_weaken_the_gate_it_is_pushed_through(repo):
    """Ветка правит страж «всегда 0» и кладёт маркер — хук зовёт копию main."""
    repo.install()
    wt = repo.worktree("feat")
    # ослабленная копия знает флаг --pre-push: отказ «старый страж» тут не спасает
    repo.write("scripts/check_private_markers.py", "import sys  # --pre-push\nsys.exit(0)\n", wt)
    repo.write("n.md", f"{MARKER}\n", wt)
    repo.commit("weaken", wt)
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0 and "PUSH ЗАБЛОКИРОВАН" in p.stderr, p.stderr


def test_foreign_author_email_is_refused_on_push(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("cherry", wt, "--author", "Someone <someone@example.com>")
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0
    assert f"автор: почта не {OWNER}" in p.stderr, p.stderr


def test_force_push_after_rebase_judges_only_new_commits(repo):
    """Коммиты main под ребейзом уже опубликованы — чужая почта в них (сквош
    GitHub) не отказывает законный push."""
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "раз\n", wt)
    repo.commit("feat 1", wt)
    assert git(wt, "push", "-q", "origin", "feat", env=repo.env, check=False).returncode == 0
    repo.write("m.md", "с сервера\n")
    repo.commit("squash", None, "--author", "GitHub <noreply@github.com>")
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "push", "-q", "origin",
        "main", env=repo.env)
    git(wt, "fetch", "-q", "origin", env=repo.env)
    git(wt, "rebase", "-q", "origin/main", env=repo.env)
    p = git(wt, "push", "--force", "origin", "feat", env=repo.env, check=False)
    assert p.returncode == 0, p.stderr
    assert "коммитов проверено: 1" in p.stdout, p.stdout


def test_branch_deleted_on_the_server_is_judged_again(repo):
    """Ветку с утечкой удалили на сервере, а remote-tracking ссылка осталась:
    повторный push обязан судить её коммиты заново (выход 2 №541, Sonnet I3)."""
    wt = repo.worktree("feat")
    repo.write("n.md", f"{MARKER}\n", wt)
    repo.commit("leak", wt)
    git(wt, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "push", "-q", "origin", "feat",
        env=repo.env)
    git(repo.remote, "branch", "-D", "feat", env=repo.env)
    assert git(wt, "rev-parse", "--verify", "-q", "refs/remotes/origin/feat",
               env=repo.env).returncode == 0      # кэш о сервере устарел
    repo.install()
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0 and "n.md:1: приватный маркер" in p.stderr, p.stdout + p.stderr


def test_ref_name_and_annotated_tag_are_published_too(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    p = git(wt, "push", "origin", f"feat:refs/heads/{MARKER}", env=repo.env, check=False)
    assert p.returncode != 0 and "приватный маркер" in p.stderr, p.stderr
    assert MARKER not in p.stderr, "имя ссылки процитировано"
    git(wt, "-c", "user.email=someone@example.com", "tag", "-a", "v1", "-m",
        f"релиз {MARKER}", env=repo.env)
    p = git(wt, "push", "origin", "v1", env=repo.env, check=False)
    assert p.returncode != 0, p.stderr
    assert "тег refs/tags/v1: приватный маркер" in p.stderr, p.stderr
    assert f"тег refs/tags/v1: почта не {OWNER}" in p.stderr, p.stderr


def test_owner_modes_read_the_default_list_not_the_env(repo):
    """На машине владельца CHAROITE_MARKERS не подменяет список (Sonnet M1)."""
    repo.write("n.md", f"{MARKER}\n")
    repo.commit("leak")
    (repo.tmp / "other.txt").write_text("совсемдругое\n", encoding="utf-8")
    env = _owner_env(repo, CHAROITE_MARKERS=str(repo.tmp / "other.txt"))
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=env)
    assert p.returncode == 1 and "n.md:1: приватный маркер" in p.stderr, p.stderr


def test_replace_refs_do_not_hide_the_real_commit(repo):
    repo.write("n.md", f"{MARKER}\n")
    bad = repo.commit("leak")
    tree = git(repo.work, "rev-parse", f"{repo.base}^{{tree}}", env=repo.env).stdout.strip()
    fake = git(repo.work, "commit-tree", tree, "-p", repo.base, "-m", "чисто",
               env=repo.env).stdout.strip()
    git(repo.work, "replace", bad, fake, env=repo.env)
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and f"{bad[:9]} n.md:1" in p.stderr, p.stdout + p.stderr


@pytest.mark.parametrize("flag", ["--range", "--pre-push"])
def test_empty_mode_argument_is_refused(repo, flag):
    p = guard_run(repo.work, flag, "", env=repo.env)
    assert p.returncode == 1 and "пустым значением" in p.stderr


def test_marker_in_a_file_name_is_masked_in_the_report(repo):
    repo.write(f"docs/{MARKER}.md", "чисто\n")
    repo.commit("name")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and "имя файла docs/***.md" in p.stderr, p.stderr
    assert MARKER not in p.stderr


def test_push_by_url_is_refused(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    p = git(wt, "push", str(repo.remote), "feat", env=repo.env, check=False)
    assert p.returncode != 0 and "не в названный remote" in p.stderr, p.stderr


def test_every_line_of_a_multi_branch_push_is_judged(repo):
    """pre-commit судит только первую ветку push нескольких; хук владельца — все."""
    repo.install()
    wt1, wt2 = repo.worktree("a1"), repo.worktree("b2")
    repo.write("ok.md", "чисто\n", wt1)
    repo.commit("ok", wt1)
    repo.write("bad.md", f"{MARKER}\n", wt2)
    repo.commit("bad", wt2)
    p = git(wt1, "push", "origin", "a1", "b2", env=repo.env, check=False)
    assert p.returncode != 0 and "bad.md:1" in p.stderr, p.stderr


def test_hook_refuses_when_the_canonical_checkout_is_off_main(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    git(repo.work, "checkout", "-q", "-b", "other", env=repo.env)
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0 and "не на ветке main" in p.stderr, p.stderr


def test_hook_refuses_a_dirty_or_stale_canonical_guard(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    canon = repo.work / "scripts" / "check_private_markers.py"
    original = canon.read_text(encoding="utf-8")
    canon.write_text(original + "\n# правка\n", encoding="utf-8")
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0 and "незакоммиченная правка стража" in p.stderr, p.stderr
    canon.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    repo.commit("старый страж")
    p = git(wt, "push", "origin", "feat", env=repo.env, check=False)
    assert p.returncode != 0 and "pull --ff-only" in p.stderr, p.stderr


def test_push_revs_skips_deletions_and_unknown_published_tips(repo):
    zero = "0" * 40
    head = repo.head()
    lines = [f"(delete) {zero} refs/heads/gone {head}",
             f"refs/heads/x {head} refs/heads/x {zero}",
             f"refs/heads/y {head} refs/heads/y {'a' * 40}"]
    cwd = os.getcwd()
    os.chdir(repo.work)
    try:
        push = guard.push_revs("origin", lines)
        # исключается то, что сервер показывает сейчас (вершина main = base)
        assert push.revs == [head, head, "--not", repo.base]
        assert guard.push_revs("origin", [lines[0]]).revs is None
    finally:
        os.chdir(cwd)


# ── установка и «нельзя забыть» ─────────────────────────────────────────────

def _owner_env(repo: Repo, **extra: str) -> dict[str, str]:
    """Машина владельца: список по умолчанию в HOME, CHAROITE_MARKERS не задан."""
    lst = repo.tmp / "home" / ".config" / "charoite" / "private_markers.txt"
    lst.parent.mkdir(parents=True, exist_ok=True)
    lst.write_text(f"{MARKER}\n", encoding="utf-8")
    env = _env(repo.tmp, markers=False)
    env.update(extra)
    return env


@pytest.mark.parametrize("ci", ["", "1"])
def test_commit_is_refused_while_the_pre_push_hook_is_missing(repo, ci):
    """Отрицательный тест проверки установки: без pre-push коммит отказан (и при
    CI=1 — проверку будит список по умолчанию, а не окружение); после
    --install-hooks — проходит."""
    repo.install()
    hook = guard.pathlib.Path(git(repo.work, "rev-parse", "--path-format=absolute",
                                  "--git-path", "hooks/pre-push", env=repo.env).stdout.strip())
    hook.unlink()
    env = _owner_env(repo, **({"CI": ci} if ci else {}))
    repo.write("a.md", "раз\n")
    git(repo.work, "add", "-A", env=env)
    p = git(repo.work, "commit", "-q", "-m", "a", env=env, check=False)
    assert p.returncode != 0 and "нет хука pre-push" in p.stderr, p.stdout + p.stderr
    hook.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    p = git(repo.work, "commit", "-q", "-m", "a", env=env, check=False)
    assert p.returncode != 0 and "не зовёт страж" in p.stderr, p.stderr
    assert guard_run(repo.work, "--install-hooks", "--force", env=env).returncode == 0
    p = git(repo.work, "commit", "-q", "-m", "a", env=env, check=False)
    assert p.returncode == 0, p.stderr


def test_markers_env_does_not_switch_the_install_check_off(repo):
    """CHAROITE_MARKERS — путь к списку, а не выключатель сверки установки."""
    repo.install()
    hooks = repo.work / ".git" / "hooks"
    (hooks / "pre-push").unlink()
    env = _owner_env(repo, CHAROITE_MARKERS=str(repo.tmp / "markers.txt"))
    repo.write("a.md", "раз\n")
    git(repo.work, "add", "-A", env=env)
    p = git(repo.work, "commit", "-q", "-m", "a", env=env, check=False)
    assert p.returncode != 0 and "нет хука pre-push" in p.stderr, p.stdout + p.stderr


def test_no_install_check_before_the_canonical_guard_knows_pre_push(repo):
    """До мержа и pull основного checkout хук ставить рано — коммиты не отказываются."""
    repo.install()
    (repo.work / ".git" / "hooks" / "pre-push").unlink()
    canon = repo.work / "scripts" / "check_private_markers.py"
    canon.write_text(canon.read_text(encoding="utf-8").replace("--pre-push", "--pre-pusk"),
                     encoding="utf-8")
    env = _owner_env(repo)
    p = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo.work, env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_pre_commit_framework_hook_with_our_legacy_counts_as_installed(repo):
    """`pre-commit install` переносит наш хук в pre-push.legacy и зовёт его сам:
    коммиты владельца не должны отказываться (выход 2 №541, Sonnet I2)."""
    repo.install()
    hooks = repo.work / ".git" / "hooks"
    (hooks / "pre-push").rename(hooks / "pre-push.legacy")
    (hooks / "pre-push").write_text("#!/usr/bin/env bash\n# pre-commit\n"
                                    "exec python3 -mpre_commit hook-impl --hook-type=pre-push\n",
                                    encoding="utf-8")
    (hooks / "pre-push").chmod(0o755)
    env = _owner_env(repo)
    repo.write("a.md", "раз\n")
    git(repo.work, "add", "-A", env=env)
    p = git(repo.work, "commit", "-q", "-m", "a", env=env, check=False)
    assert p.returncode == 0, p.stderr
    (hooks / "pre-push.legacy").unlink()
    repo.write("b.md", "два\n")
    git(repo.work, "add", "-A", env=env)
    p = git(repo.work, "commit", "-q", "-m", "b", env=env, check=False)
    assert p.returncode != 0 and "не зовёт страж" in p.stderr, p.stderr


def test_install_hooks_only_from_the_main_checkout_and_never_clobbers(repo):
    wt = repo.worktree("feat")
    p = guard_run(wt, "--install-hooks", env=repo.env)
    assert p.returncode == 1 and "основного checkout на main" in p.stderr
    hooks = repo.work / ".git" / "hooks"
    (hooks / "pre-push").write_text("#!/bin/bash\n# свой\n", encoding="utf-8")
    p = guard_run(repo.work, "--install-hooks", env=repo.env)
    assert p.returncode == 1 and "не затираю" in p.stderr
    assert (hooks / "pre-push").read_text(encoding="utf-8") == "#!/bin/bash\n# свой\n"
    assert (hooks / "pre-commit").read_text(encoding="utf-8") == guard.PRE_COMMIT_HOOK
    p = guard_run(repo.work, "--install-hooks", "--force", env=repo.env)
    assert p.returncode == 0
    assert (hooks / "pre-push").read_text(encoding="utf-8") == guard.PRE_PUSH_HOOK
    assert (hooks / "pre-push.bak").read_text(encoding="utf-8") == "#!/bin/bash\n# свой\n"
    assert os.access(hooks / "pre-push", os.X_OK)
    p = guard_run(repo.work, "--install-hooks", env=repo.env)
    assert p.returncode == 0 and p.stdout.count("уже стоит") == 2


def test_install_refuses_when_core_hooks_path_is_set(repo):
    git(repo.work, "config", "core.hooksPath", str(repo.tmp / "global-hooks"), env=repo.env)
    p = guard_run(repo.work, "--install-hooks", env=repo.env)
    assert p.returncode == 1 and "core.hooksPath" in p.stderr
    assert not (repo.tmp / "global-hooks").exists()


# ── контрибьюторы: стадия pre-push pre-commit ───────────────────────────────

def test_range_from_env_needs_the_pre_push_environment(repo):
    p = guard_run(repo.work, "--range-from-env", env=repo.env)
    assert p.returncode == 1 and "PRE_COMMIT_REMOTE_NAME" in p.stderr


def test_range_from_env_without_a_list_checks_public_formats(repo, tmp_path):
    repo.write("cfg.txt", f"{LEAK_PATH}\n")
    head = repo.commit("cfg")
    env = _env(tmp_path, markers=False, PRE_COMMIT_REMOTE_NAME="origin",
               PRE_COMMIT_TO_REF=head, PRE_COMMIT_FROM_REF=repo.base)
    p = guard_run(repo.work, "--range-from-env", env=env)
    assert p.returncode == 1 and "личный путь" in p.stderr, p.stderr
    repo.write("cfg.txt", "чисто\n")
    git(repo.work, "commit", "-q", "--amend", "-a", "-m", "cfg", env=repo.env)
    env["PRE_COMMIT_TO_REF"] = repo.head()
    p = guard_run(repo.work, "--range-from-env", env=env)
    assert p.returncode == 0 and "публичные форматы" in p.stdout, p.stderr


def test_pre_commit_config_wires_the_push_stage():
    """Сторож проводки: без этих ключей `pre-commit install` pre-push не ставит,
    а прочие хуки поехали бы на push."""
    import yaml
    cfg = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    assert cfg["default_install_hook_types"] == ["pre-commit", "pre-push"]
    assert cfg["default_stages"] == ["pre-commit"]
    assert cfg["minimum_pre_commit_version"] == "3.2.0"
    hooks = {h["id"]: h for r in cfg["repos"] for h in r["hooks"]}
    assert hooks["private-markers"]["stages"] == ["pre-commit"]
    push = hooks["private-markers-push"]
    assert push["stages"] == ["pre-push"]
    assert push["entry"].endswith("check_private_markers.py --range-from-env")


# ── мутации CI (выход 2 №541): каждое правило ниже пинит выжившего мутанта ──

def test_clip_keeps_sixty_characters():
    assert guard._clip("я" * 100) == "я" * 60


def test_git_error_names_what_git_said(repo):
    p = guard_run(repo.work, "--range", "deadbeef..HEAD", env=repo.env)
    assert p.returncode == 1 and "deadbeef" in p.stderr and "git log -z:" in p.stderr, p.stderr


def test_git_ok_stays_quiet(repo, capfd, monkeypatch):
    monkeypatch.chdir(repo.work)
    assert guard.git_ok("rev-parse", "--verify", "нет-такой-ревизии") is False
    assert capfd.readouterr().err == ""


def test_every_escape_of_a_quoted_path_is_undone(repo):
    names = ["таб\tа.md", "слэш\\а.md", "воз\rврат.md", "звон\aок.md", "за\bбой.md",
             "пере\fвод.md", "верт\vикаль.md", "код\x01.md"]
    for n in names:
        (repo.work / n).write_text(f"{MARKER}\n", encoding="utf-8")
    repo.commit("esc")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    for n in names:   # text=True переводит \r вывода в \n
        assert f"{n.replace(chr(13), chr(10))}:1: приватный маркер" in p.stderr, (repr(n), p.stderr)


def test_second_hunk_of_one_file_is_read(repo):
    repo.write("long.md", "".join(f"строка {i}\n" for i in range(40)))
    repo.commit("base")
    lines = [f"строка {i}\n" for i in range(40)]
    lines[2] = "правка\n"
    lines[30] = f"{MARKER}\n"
    repo.write("long.md", "".join(lines))
    bad = repo.commit("two hunks")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert f"{bad[:9]} long.md:31: приватный маркер" in p.stderr, p.stderr


def test_line_number_after_a_last_line_without_newline(repo):
    (repo.work / "tail.md").write_text("раз\nдва", encoding="utf-8")
    repo.commit("tail")
    (repo.work / "tail.md").write_text(f"раз\n{MARKER}\nтри", encoding="utf-8")
    repo.commit("edit")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert "tail.md:2: приватный маркер" in p.stderr and "tail.md:3:" not in p.stderr, p.stderr


def test_deleting_a_published_file_is_not_a_finding(repo):
    """Удаление не публикует имя: имена удалённых файлов не судятся."""
    repo.write(f"{MARKER}.md", "старое\n")
    repo.commit("было до списка")
    base = repo.head()
    (repo.work / f"{MARKER}.md").unlink()
    repo.commit("удалил")
    p = guard_run(repo.work, "--range", f"{base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr


def test_url_refusal_names_the_known_remotes(repo):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    p = git(wt, "push", str(repo.remote), "feat", env=repo.env, check=False)
    assert "(origin)" in p.stderr, p.stderr


def test_mirror_push_does_not_rejudge_what_origin_already_published(repo):
    """Push в пустое зеркало: история, уже опубликованная на origin (в ней
    веб-слияния с коммитером GitHub), не судится заново; новое — судится
    (финальный круг №541, Opus I2)."""
    repo.write("web.md", "слияние\n")
    git(repo.work, "add", "-A", env=repo.env)
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "-c", "user.name=GitHub",
        "-c", "user.email=noreply@github.com", "commit", "-q", "-m", "web merge", env=repo.env)
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "push", "-q", "origin",
        "main", env=repo.env)
    empty = repo.tmp / "empty.git"
    git(repo.tmp, "init", "-q", "--bare", str(empty), env=repo.env)
    git(repo.work, "remote", "add", "mirror", str(empty), env=repo.env)
    repo.install()
    p = git(repo.work, "push", "mirror", "main", env=repo.env, check=False)
    assert p.returncode == 0 and "коммитов проверено: 0" in p.stdout, p.stdout + p.stderr
    repo.write("new.md", "своё\n")
    repo.commit("new")
    p = git(repo.work, "push", "mirror", "main", env=repo.env, check=False)
    assert p.returncode == 0 and "коммитов проверено: 1" in p.stdout, p.stdout + p.stderr


def test_owner_signed_annotated_tag_pushes(repo):
    repo.install()
    git(repo.work, "tag", "-a", "v2", "-m", "релиз", env=repo.env)
    p = git(repo.work, "push", "origin", "v2", env=repo.env, check=False)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("have", ["remote", "to"])
def test_range_from_env_needs_both_remote_and_ref(repo, tmp_path, have):
    extra = ({"PRE_COMMIT_REMOTE_NAME": "origin"} if have == "remote"
             else {"PRE_COMMIT_TO_REF": repo.head()})
    p = guard_run(repo.work, "--range-from-env", env=_env(tmp_path, markers=False, **extra))
    assert p.returncode == 1 and "PRE_COMMIT_REMOTE_NAME" in p.stderr


@pytest.mark.parametrize("which", ["pre-push", "pre-push.legacy"])
def test_non_executable_hook_is_a_problem(repo, which):
    repo.install()
    hooks = repo.work / ".git" / "hooks"
    if which == "pre-push.legacy":
        (hooks / "pre-push").rename(hooks / "pre-push.legacy")
        (hooks / "pre-push").write_text("#!/bin/sh\n# hook-impl\n", encoding="utf-8")
        (hooks / "pre-push").chmod(0o755)
    (hooks / which).chmod(0o644)
    env = _owner_env(repo)
    repo.write("a.md", "раз\n")
    git(repo.work, "add", "-A", env=env)
    p = git(repo.work, "commit", "-q", "-m", "a", env=env, check=False)
    assert p.returncode != 0 and "не исполняемый" in p.stderr, p.stderr


def test_install_refuses_from_the_main_checkout_on_another_branch(repo):
    git(repo.work, "checkout", "-q", "-b", "other", env=repo.env)
    p = guard_run(repo.work, "--install-hooks", env=repo.env)
    assert p.returncode == 1 and "на other" in p.stderr, p.stderr


@pytest.mark.parametrize("count,tail", [(50, False), (51, True)])
def test_report_lists_fifty_and_counts_the_rest(repo, count, tail):
    repo.write("many.md", f"{MARKER}\n" * count)
    repo.commit("many")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert ("  … ещё 1\n" in p.stderr) is tail and ("… ещё" in p.stderr) is tail, p.stderr


def test_empty_marker_list_is_a_refusal(repo):
    (repo.tmp / "markers.txt").write_text("# только комментарий\n", encoding="utf-8")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and "пуст" in p.stderr, p.stderr


def test_run_commits_returns_zero_explicitly(repo, monkeypatch):
    monkeypatch.chdir(repo.work)
    monkeypatch.setenv("CHAROITE_MARKERS", str(repo.tmp / "markers.txt"))
    monkeypatch.setenv("HOME", str(repo.tmp / "home"))
    assert guard.run_commits(None, need_list=True, identity=True, notes=[]) == 0
    repo.write("a.md", "раз\n")
    repo.commit("a")
    assert guard.run_commits([f"{repo.base}..HEAD"], need_list=True, identity=True,
                             notes=[]) == 0


def test_range_does_not_require_the_owner_address(repo):
    repo.write("a.md", "раз\n")
    repo.commit("чужой", None, "--author", "Someone <someone@example.com>")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr


def test_pre_push_without_the_list_is_refused(repo, tmp_path):
    repo.install()
    wt = repo.worktree("feat")
    repo.write("n.md", "чисто\n", wt)
    repo.commit("ok", wt)
    env = _env(tmp_path, CHAROITE_MARKERS=str(tmp_path / "нет.txt"))
    p = git(wt, "push", "origin", "feat", env=env, check=False)
    assert p.returncode != 0 and "fail-closed" in p.stderr, p.stderr


def test_contributor_address_is_not_judged(repo, tmp_path):
    repo.write("a.md", "раз\n")
    head = repo.commit("чужой", None, "--author", "Someone <someone@example.com>")
    env = _env(tmp_path, markers=False, PRE_COMMIT_REMOTE_NAME="origin", PRE_COMMIT_TO_REF=head)
    p = guard_run(repo.work, "--range-from-env", env=env)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("ci", ["", "1"])
def test_pre_commit_author_check_is_skipped_only_in_ci(repo, ci):
    git(repo.work, "config", "user.email", "someone@example.com", env=repo.env)
    env = dict(repo.env, **({"CI": ci} if ci else {}))
    p = guard_run(repo.work, env=env)
    assert (p.returncode == 0) is bool(ci), p.stderr


def test_journal_line_that_is_not_a_commit_is_named(monkeypatch):
    monkeypatch.setattr(guard, "git", lambda *a, **k: b"\x01notasha\n")
    with pytest.raises(guard.GitError, match="— 'notasha'"):
        list(guard.added_lines(["x"]))


def test_file_name_starting_with_the_record_mark(repo):
    """Имя файла вправе начинаться с \x01 — это поле, а не граница коммита."""
    (repo.work / "\x01лишнее.md").write_text("чисто\n", encoding="utf-8")
    (repo.work / ("f" + "0" * 40)).write_text("чисто\n", encoding="utf-8")
    (repo.work / f"я{MARKER}.md").write_text("чисто\n", encoding="utf-8")
    sha = repo.commit("mark")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    hits = [ln.strip() for ln in p.stderr.splitlines() if "имя файла" in ln]
    assert hits == [f"{sha[:9]} имя файла я***.md: приватный маркер"], p.stdout + p.stderr


def test_nfd_text_matches_an_nfc_marker(repo):
    import unicodedata
    (repo.tmp / "markers.txt").write_text("Андрейка\n", encoding="utf-8")
    repo.write("nfd.md", unicodedata.normalize("NFD", "привет, Андрейка\n"))
    repo.commit("nfd")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert "nfd.md:1: приватный маркер" in p.stderr, p.stderr
    base = repo.head()
    repo.write("n2.md", unicodedata.normalize("NFD", "Семёнов " + "И. И.\n"))
    repo.commit("nfd2")
    p = guard_run(repo.work, "--range", f"{base}..HEAD", env=repo.env)
    assert "n2.md:1: фамилия с инициалами" in p.stderr, p.stderr


def test_refusals_do_not_quote_names_or_url_secrets(repo, monkeypatch):
    (repo.work / f"{MARKER}.bin").write_bytes(b"\x00" * 64)
    repo.commit("bin")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and MARKER not in p.stderr and "***.bin (двоичный)" in p.stderr
    monkeypatch.chdir(repo.work)
    with pytest.raises(guard.GitError) as e:
        guard.push_revs("https://" + "user:" + "tok" + "@example.com/r.git", [])
    shown = str(e.value).split("'")[1]
    assert shown == "https://example.com/r.git", str(e.value)


def test_pre_commit_names_the_place_not_the_line(repo):
    """Маркер только в индексе (рабочий файл чист): судит дифф коммита, а не
    проход по дереву, — и называет место, а не строку."""
    repo.write("leak.md", f"раз\n{MARKER}\n")
    git(repo.work, "add", "-A", env=repo.env)
    repo.write("leak.md", "раз\nчисто\n")
    p = guard_run(repo.work, env=repo.env)
    assert p.returncode == 1 and "leak.md:2" in p.stderr and MARKER not in p.stderr, p.stderr


def test_push_url_decides_what_is_published(repo):
    """pushurl ведёт в другой репозиторий, чем fetch: опубликованное — там, куда
    пушат (финальный круг №541, Opus M3)."""
    git(repo.work, "checkout", "-q", "-b", "leak", env=repo.env)
    repo.write("n.md", f"{MARKER}\n")
    repo.commit("leak")
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "push", "-q", "origin",
        "leak", env=repo.env)
    git(repo.work, "checkout", "-q", "main", env=repo.env)
    public = repo.tmp / "public.git"
    git(repo.tmp, "init", "-q", "--bare", str(public), env=repo.env)
    git(repo.work, "config", "remote.origin.pushurl", str(public), env=repo.env)
    repo.install()
    p = git(repo.work, "push", "origin", "leak", env=repo.env, check=False)
    assert p.returncode != 0 and "n.md:1: приватный маркер" in p.stderr, p.stdout + p.stderr


def test_mergetag_header_is_judged(repo):
    """Слияние подписанного тега хранит его текст в заголовке mergetag вне %B."""
    tree = git(repo.work, "rev-parse", "HEAD^{tree}", env=repo.env).stdout.strip()
    raw = (f"tree {tree}\nparent {repo.base}\n"
           f"author A <{OWNER}> 1700000000 +0000\ncommitter A <{OWNER}> 1700000000 +0000\n"
           f"mergetag object {repo.base}\n type commit\n tag v9\n"
           f" tagger A <{OWNER}> 1700000000 +0000\n \n релиз {MARKER}\n"
           f"\nчистое сообщение\n")
    sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo.work,
                         env=repo.env, input=raw, capture_output=True, text=True).stdout.strip()
    git(repo.work, "update-ref", "refs/heads/main", sha, env=repo.env)
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert f"{sha[:9]} заголовок mergetag: приватный маркер" in p.stderr, p.stdout + p.stderr


def test_signature_headers_are_not_judged():
    raw = ("tree a\nparent b\nauthor x\ncommitter y\ngpgsig -----BEGIN-----\n " + SURNAME_INITIALS + "\n"
           " -----END-----\nmergetag object c\n tag v\n\nmsg\n")
    assert guard.extra_headers(raw) == [("mergetag", "object c\ntag v")]


def test_tag_on_a_tag_is_judged_whole(repo):
    repo.install()
    git(repo.work, "tag", "-a", "inner", "-m", f"внутри {MARKER}", env=repo.env)
    git(repo.work, "tag", "-a", "outer", "-m", "снаружи", "inner", env=repo.env)
    p = git(repo.work, "push", "origin", "outer", env=repo.env, check=False)
    assert p.returncode != 0 and "тег refs/tags/outer: приватный маркер" in p.stderr, p.stderr


def test_unknown_patch_header_is_a_refusal():
    with pytest.raises(guard.GitError, match="заголовок"):
        list(guard.parse_patch("diff --git a/x c/x\n+++ c/x\n@@ -0,0 +1 @@\n+y\n", "s"))


def test_binary_whose_name_git_cannot_hand_back_is_a_refusal(repo):
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo.work, env=repo.env,
                          input=b"\x00bin", capture_output=True).stdout.decode().strip()
    subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                    f"100644,{blob},".encode() + b"\xff.dat"], cwd=repo.work, env=repo.env,
                   check=True)
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "commit", "-q", "-m", "ff",
        env=repo.env)
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and "не найден" in p.stderr, p.stdout + p.stderr


def test_tree_scan_normalizes_nfd(tmp_path, monkeypatch):
    import unicodedata
    f = tmp_path / "t.md"
    f.write_text(unicodedata.normalize("NFD", "Семёнов " + "И. И.\n"), encoding="utf-8")
    assert guard.scan_public([f]) == [f"{f}:1: фамилия с инициалами"]


# ── узкий круг Sonnet по правкам Opus (№541) ────────────────────────────────

def _zip(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_mirror_push_takes_origin_by_its_push_url(repo):
    """origin тянет из одного репозитория, а пушит в публичный: для push в
    третий remote «опубликованное» — вершины публичного."""
    git(repo.work, "checkout", "-q", "-b", "leak", env=repo.env)
    repo.write("n.md", f"{MARKER}\n")
    repo.commit("leak")
    git(repo.work, "-c", f"core.hooksPath={repo.tmp / 'no-hooks'}", "push", "-q", "origin",
        "leak", env=repo.env)
    git(repo.work, "checkout", "-q", "main", env=repo.env)
    public = repo.tmp / "public.git"
    git(repo.tmp, "init", "-q", "--bare", str(public), env=repo.env)
    git(repo.work, "config", "remote.origin.pushurl", str(public), env=repo.env)
    mirror = repo.tmp / "mirror.git"
    git(repo.tmp, "init", "-q", "--bare", str(mirror), env=repo.env)
    git(repo.work, "remote", "add", "mirror", str(mirror), env=repo.env)
    repo.install()
    p = git(repo.work, "push", "mirror", "leak", env=repo.env, check=False)
    assert p.returncode != 0 and "n.md:1: приватный маркер" in p.stderr, p.stdout + p.stderr




# ── двоичное содержимое: отказ или решение владельца (выход №541, круг 3) ──

def _allow(repo, *blobs: str) -> None:
    path = repo.tmp / "home" / ".config" / "charoite" / "blob_allow.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# решено публиковать\n" + "".join(f"{b}  иконка\n" for b in blobs),
                    encoding="utf-8")


def test_binary_outside_media_is_refused_with_its_blob_hash(repo):
    """Один NUL делает файл двоичным для git — и страж не пытается угадать, что
    внутри: отказ с хешем блоба (так закрыт и обход «добавь NUL — и строки не
    видны»)."""
    (repo.work / "data.txt").write_bytes(f"запуск на {MARKER}\n".encode() + b"\x00tail\n")
    sha = repo.commit("nul")
    blob = git(repo.work, "rev-parse", f"{sha}:data.txt", env=repo.env).stdout.strip()
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1, p.stderr
    assert f"{sha[:9]} data.txt (двоичный): содержимое не проверить" in p.stderr, p.stderr
    assert blob in p.stderr and "blob_allow.txt" in p.stderr


def test_allowed_blob_passes_and_only_that_blob(repo):
    (repo.work / "AppIcon.icns").write_bytes(b"icns\x00\x01\x02")
    first = repo.commit("icon")
    _allow(repo, git(repo.work, "rev-parse", f"{first}:AppIcon.icns", env=repo.env).stdout.strip())
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr
    (repo.work / "AppIcon.icns").write_bytes(b"icns\x00\x01\x03")
    repo.commit("icon v2")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and "AppIcon.icns (двоичный)" in p.stderr, p.stderr


def test_media_binary_is_judged_by_name_only(repo):
    (repo.work / "shot.png").write_bytes(b"\x89PNG\x00" + MARKER.encode())
    repo.commit("png")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr


def test_zip_is_not_media_for_the_per_commit_guard(repo):
    (repo.work / "data.zip").write_bytes(_zip({"notes.txt": b"x"}))
    repo.commit("zip")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1 and "data.zip (двоичный)" in p.stderr, p.stderr


def test_allow_list_comments_and_blank_lines_are_not_hashes(repo, monkeypatch):
    _allow(repo, "a" * 40)
    monkeypatch.setenv("HOME", str(repo.tmp / "home"))
    assert guard.allowed_blobs() == {"a" * 40}


def test_deleting_a_published_binary_is_not_a_refusal(repo):
    (repo.work / "old.bin").write_bytes(b"\x00\x01")
    repo.commit("было")
    base = repo.head()
    (repo.work / "old.bin").unlink()
    repo.commit("удалил")
    p = guard_run(repo.work, "--range", f"{base}..HEAD", env=repo.env)
    assert p.returncode == 0, p.stderr
