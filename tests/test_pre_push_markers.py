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


def test_nul_byte_does_not_hide_a_line(repo):
    """Один нулевой байт делает файл «двоичным» для git: патч идёт с --text."""
    repo.write("data.txt", f"{MARKER}\n")
    (repo.work / "data.txt").write_bytes(f"{MARKER}\n".encode() + b"\x00tail\n")
    repo.commit("nul")
    p = guard_run(repo.work, "--range", f"{repo.base}..HEAD", env=repo.env)
    assert p.returncode == 1
    assert "data.txt:1: приватный маркер" in p.stderr, p.stderr


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


def test_git_error_is_a_refusal_not_a_clean_pass(repo):
    p = guard_run(repo.work, "--range", "deadbeef..HEAD", env=repo.env)
    assert p.returncode == 1
    assert "не смог проверить" in p.stderr


def test_range_without_marker_list_refuses_outside_ci(repo, tmp_path):
    env = _env(tmp_path, CHAROITE_MARKERS=str(tmp_path / "нет-списка.txt"))
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
        revs, _ = guard.push_revs("origin", lines)
        assert revs == [head, head, "--not", "--remotes=origin"]
        assert guard.push_revs("origin", [lines[0]])[0] is None
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
    assert os.access(hooks / "pre-push", os.X_OK)
    p = guard_run(repo.work, "--install-hooks", env=repo.env)
    assert p.returncode == 0 and p.stdout.count("уже стоит") == 2


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
