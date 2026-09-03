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
