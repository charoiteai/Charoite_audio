"""Правленую руками стенограмму пересборка не распознаёт заново.

№131: человек правит финальную стенограмму (имена, термины) и жмёт
«Пересобрать результат», чтобы минутки собрались по правленому тексту.
До этого rebuild() гнал STT по записям заново: правки уезжали в .prev, а
минутки собирались по машинному тексту. Признак правки — хеш стенограммы
из live.json (снимает write_final) не совпал с файлом; без хеша (старые
встречи, первая пересборка живого черновика) — распознаём, как прежде.
"""
import hashlib
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import graph_updater  # noqa: E402
import live_sidecar  # noqa: E402
import rebuild_transcript as rt  # noqa: E402

CFG = {"audio": {"samplerate": 16000}, "log": {}}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "transcripts").mkdir()
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    # Записей в tmp нет — путь STT сразу отвечает «записей нет», без
    # 45-секундных ожиданий канала.
    monkeypatch.setattr(rt, "wait_recording", lambda *a, **k: None)
    return tmp_path


def _meeting(root: pathlib.Path, text: str, meta: dict | None) -> pathlib.Path:
    live = root / "transcripts" / "2026-09-03_1200.md"
    live.write_text(text, encoding="utf-8")
    if meta is not None:
        live.with_name(live.name + ".live.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return live


class _NoSTT:
    def __init__(self, *a, **k):
        raise AssertionError("STT не должен подниматься для правленой стенограммы")


def test_edited_transcript_skips_stt_and_rebuilds_minutes(root, monkeypatch):
    live = _meeting(root, "**Анна** [12:00]:\nправленый руками текст\n",
                    {"transcript_sha256": _sha("машинный текст")})
    got: dict = {}
    monkeypatch.setattr(rt, "STT", _NoSTT)
    monkeypatch.setattr(rt, "finalize_minutes",
                        lambda l, text, meta, cfg, names: got.setdefault("text", text) and False)
    monkeypatch.setattr(rt, "canonize_file", lambda *a, **k: None)
    assert rt.rebuild(live, CFG) == live
    assert got["text"] == live.read_text(encoding="utf-8"), "минутки — по правленому тексту"
    assert live.read_text(encoding="utf-8").startswith("**Анна**"), "стенограмма не тронута"
    assert not (live.parent / ".prev").exists(), "правки не уезжают в .prev"


def test_machine_transcript_goes_to_stt(root, monkeypatch):
    text = "машинный текст\n"
    live = _meeting(root, text, {"transcript_sha256": _sha(text)})
    monkeypatch.setattr(rt, "finalize_minutes", lambda *a, **k: pytest.fail("минутки без STT"))
    # хеш совпал → обычный путь → записей нет → None
    assert rt.rebuild(live, CFG) is None


def test_no_hash_means_recognize_as_before(root, monkeypatch):
    live = _meeting(root, "текст без хеша\n", {"speakers": {}})
    monkeypatch.setattr(rt, "finalize_minutes", lambda *a, **k: pytest.fail("минутки без STT"))
    assert rt.rebuild(live, CFG) is None


def test_write_final_records_transcript_hash(root):
    live = _meeting(root, "живой черновик\n", {})
    rt.write_final(live, "финал\n", "живой черновик\n")
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == _sha("финал\n")
    assert rt.human_edited_transcript(live, meta) is None
    live.write_text("финал, правленный\n", encoding="utf-8")
    assert rt.human_edited_transcript(live, meta) == "финал, правленный\n"


def test_edited_path_rebuilds_minutes_file_and_hashes(root, monkeypatch):
    edited = "**Анна** [12:00]:\nправленый текст\n"
    live = _meeting(root, edited, {"transcript_sha256": _sha("машинный текст")})
    mpath = live.with_name(live.stem + "_minutes.md")

    def fake_finalize(l, text, meta, cfg, names):
        mpath.write_text("# Протокол\n" + text, encoding="utf-8")
        return True
    monkeypatch.setattr(rt, "STT", _NoSTT)
    monkeypatch.setattr(rt, "finalize_minutes", fake_finalize)
    monkeypatch.setattr(rt, "canonize_file", lambda *a, **k: None)
    assert rt.rebuild(live, CFG) == live
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == _sha("машинный текст"), "хеш стенограммы не трогаем — файл правленый"
    assert meta["minutes_sha256"] == _sha(mpath.read_text(encoding="utf-8"))
    assert meta["minutes_source_sha256"] == _sha(edited)


def test_second_click_without_changes_does_nothing(root, monkeypatch):
    edited = "правленый текст\n"
    live = _meeting(root, edited, {"transcript_sha256": _sha("машинный текст"),
                                   "minutes_source_sha256": _sha(edited)})
    monkeypatch.setattr(rt, "STT", _NoSTT)
    monkeypatch.setattr(rt, "finalize_minutes", lambda *a, **k: pytest.fail("минутки перегенерированы без изменений"))
    assert rt.rebuild(live, CFG) == live


def test_retitle_refreshes_transcript_hash(root):
    """Накат темы меняет шапку и имя файла — это машинная запись, хеш
    освежается, вторая пересборка не принимает тему за правку руками."""
    bare = "2026-09-03_120005"
    live = root / "transcripts" / f"{bare}.md"
    live.write_text("живой черновик\n", encoding="utf-8")
    live.with_name(live.name + ".live.json").write_text("{}", encoding="utf-8")
    rt.write_final(live, f"# Встреча {bare}\n\n**Анна** [12:00]:\nтекст\n", "живой черновик\n")
    titled = graph_updater.retitle(live, "2026-09-03_1200", bare, "План выпуска")
    assert titled != live and titled.exists()
    assert titled.read_text(encoding="utf-8").startswith("# Встреча 2026-09-03_1200 — План выпуска")
    meta = rt.live_meta(titled)
    assert rt.human_edited_transcript(titled, meta) is None, "тема в шапке — не правка руками"
    titled.write_text(titled.read_text(encoding="utf-8") + "правка\n", encoding="utf-8")
    assert rt.human_edited_transcript(titled, meta) is not None


def test_two_sidecars_in_one_minute_disable_the_gate(root, monkeypatch):
    live = root / "transcripts" / "2026-09-03_1200_Тема.md"
    live.write_text("текст\n", encoding="utf-8")
    for bare in ("2026-09-03_120005", "2026-09-03_120045"):
        (root / "transcripts" / f"{bare}.md.live.json").write_text(
            json.dumps({"transcript_sha256": _sha("другое")}), encoding="utf-8")
    assert live_sidecar.sidecar_for(live) is None
    assert rt.human_edited_transcript(live, {"transcript_sha256": _sha("другое")}) is None
    assert live_sidecar.remember(live, "transcript_sha256", _sha("текст\n")) is False


def test_missing_sidecar_is_created_and_garbage_hash_ignored(root):
    live = _meeting(root, "живой\n", None)
    rt.write_final(live, "финал\n", "живой\n")
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == _sha("финал\n")
    assert rt.human_edited_transcript(live, {"transcript_sha256": 123}) is None
    assert rt.human_edited_transcript(live, {"transcript_sha256": "abc"}) is None


def test_force_stt_env_skips_the_gate(root, monkeypatch):
    live = _meeting(root, "правленый\n", {"transcript_sha256": _sha("машинный")})
    monkeypatch.setenv("CHAROITE_FORCE_STT", "1")
    assert rt.human_edited_transcript(live, rt.live_meta(live)) is None
