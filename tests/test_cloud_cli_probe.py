"""Зонд Claude CLI перед ночными облачными шагами (аудит GLM/DS 05.09).

Ночь 05.09: обновление CLI подменило бинарник, exec падал с «Exec format
error», семь тем ревизии легли в отчёт как «сбой» при коде 0 у ночи.
"""
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import cloud  # noqa: E402


def test_probe_returns_none_on_exec_failure(monkeypatch):
    def boom(*_a, **_k):
        raise OSError(8, "Exec format error")
    monkeypatch.setattr(cloud.subprocess, "run", boom)
    assert cloud.probe_claude("/x/claude") is None


def test_checked_bin_retries_once_then_gives_up(monkeypatch):
    calls = []

    def probe(path, timeout=30):
        calls.append(path)
        return None

    monkeypatch.setattr(cloud, "probe_claude", probe)
    monkeypatch.setattr(cloud.shutil, "which", lambda _n: "/usr/local/bin/claude")
    slept = []
    monkeypatch.setattr(cloud.time, "sleep", lambda s: slept.append(s))
    with pytest.raises(cloud.CloudCLIUnavailable):
        cloud.claude_bin_checked(pause=0)
    assert slept == [0], "одна пауза между двумя попытками"
    assert calls == ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"] * 2


def test_checked_bin_takes_the_second_candidate_when_the_first_is_broken(monkeypatch):
    monkeypatch.setattr(cloud.shutil, "which", lambda _n: "/usr/local/bin/claude")
    monkeypatch.setattr(cloud, "probe_claude",
                        lambda p, timeout=30: "2.1.261" if p.startswith("/opt") else None)
    assert cloud.claude_bin_checked(pause=0) == "/opt/homebrew/bin/claude"


def test_dossier_review_exits_3_when_cli_is_down(monkeypatch, capsys):
    import nightly_dossier_review as review

    monkeypatch.setattr(sys, "argv", ["nightly_dossier_review.py", "--all-graphs"])
    monkeypatch.setattr(review, "_cfg", lambda: {})
    monkeypatch.setattr(review.privacy, "cloud_enrich_enabled", lambda cfg: True)

    def down():
        raise cloud.CloudCLIUnavailable("битый симлинк")
    monkeypatch.setattr(review.cloud, "claude_bin_checked", down)
    assert review.main() == 3
    assert "не начата" in capsys.readouterr().out


def test_dossier_review_exits_2_when_some_themes_failed(monkeypatch):
    import nightly_dossier_review as review

    monkeypatch.setattr(sys, "argv", ["nightly_dossier_review.py", "--all-graphs"])
    monkeypatch.setattr(review, "_cfg", lambda: {})
    monkeypatch.setattr(review.privacy, "cloud_enrich_enabled", lambda cfg: True)
    monkeypatch.setattr(review.cloud, "claude_bin_checked", lambda: "/x/claude")
    monkeypatch.setattr(review.graphs, "all_graphs", lambda marker: [])
    review.FAILED_STEPS.clear()
    assert review.main() == 0
    review.FAILED_STEPS.append("- **тема** — сбой: claude не отработал")
    try:
        assert review.main() == 2
    finally:
        review.FAILED_STEPS.clear()


DOSSIER = "## Сейчас\nстарое\n\n## Источники\n- [[Встречи/2026-09-01_1000]]\n\n## Правки автора\n\n—\n"


def _review_call(monkeypatch, run):
    import nightly_dossier_review as review

    monkeypatch.setattr(review.subprocess, "run", run)
    slept = []
    monkeypatch.setattr(review.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(review.cloud, "claude_bin", lambda: "/x/claude")
    monkeypatch.setattr(review.cloud, "add_proxy", lambda env: None)
    monkeypatch.setattr(review.privacy, "cloud_enrich_enabled", lambda cfg: True)
    monkeypatch.setattr(review.dossier, "trim_to_format", lambda t: t)
    return review, slept


def test_review_retries_exec_failure_once(monkeypatch, tmp_path):
    """Первая попытка — Exec format error, вторая — ответ: тема не теряется."""
    attempts = []

    def run(cmd, **kw):
        attempts.append(cmd[0])
        if len(attempts) == 1:
            raise OSError(8, "Exec format error")
        return subprocess.CompletedProcess(cmd, 0, stdout="## Сейчас\nновое\n", stderr="")

    review, slept = _review_call(monkeypatch, run)
    fixed, why = review.review("Тема", tmp_path / "Тема.md", tmp_path, {}, [], "opus", {}, current=DOSSIER)
    assert len(attempts) == 2 and slept == [review.cloud.RETRY_PAUSE], "одна пауза и повтор"
    assert not why.startswith("сбой:"), why


def test_review_gives_up_after_the_second_exec_failure(monkeypatch, tmp_path):
    def run(cmd, **kw):
        raise OSError(8, "Exec format error")

    review, _ = _review_call(monkeypatch, run)
    fixed, why = review.review("Тема", tmp_path / "Тема.md", tmp_path, {}, [], "opus", {}, current=DOSSIER)
    assert fixed is None and why.startswith("сбой:")
