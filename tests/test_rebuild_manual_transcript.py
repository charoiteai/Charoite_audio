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
                        lambda l, text, meta, cfg, names: got.setdefault("text", text) and "human")
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
        return "regenerated"
    monkeypatch.setattr(rt, "STT", _NoSTT)
    monkeypatch.setattr(rt, "finalize_minutes", fake_finalize)
    monkeypatch.setattr(rt, "canonize_file", lambda *a, **k: None)
    assert rt.rebuild(live, CFG) == live
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == _sha("машинный текст"), "хеш стенограммы не трогаем — файл правленый"
    assert meta["minutes_sha256"] == _sha(mpath.read_text(encoding="utf-8"))
    assert meta["minutes_source_sha256"] == _sha(edited)   # речь без «Ко-мышления» = весь текст


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
    assert titled.with_name(titled.name + ".live.json").exists(), "сайдкар переехал вместе с файлом"
    assert not live.with_name(live.name + ".live.json").exists()
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


def test_retitle_keeps_a_human_edit_protected(root):
    """Правка руками, потом накат темы: хеш не освежается, правка под защитой."""
    bare = "2026-09-03_120005"
    live = root / "transcripts" / f"{bare}.md"
    live.write_text("живой\n", encoding="utf-8")
    live.with_name(live.name + ".live.json").write_text("{}", encoding="utf-8")
    rt.write_final(live, f"# Встреча {bare}\n\nмашинный текст\n", "живой\n")
    live.write_text(f"# Встреча {bare}\n\nправленый текст\n", encoding="utf-8")
    titled = graph_updater.retitle(live, "2026-09-03_1200", bare, "Тема")
    assert rt.human_edited_transcript(titled, rt.live_meta(titled)) is not None


def test_restamp_does_not_record_minutes_source(root, monkeypatch):
    edited = "правленый текст\n"
    live = _meeting(root, edited, {"transcript_sha256": _sha("машинный текст")})
    monkeypatch.setattr(rt, "STT", _NoSTT)
    monkeypatch.setattr(rt, "finalize_minutes", lambda *a, **k: "restamped")
    monkeypatch.setattr(rt, "canonize_file", lambda *a, **k: None)
    live.with_name(live.stem + "_minutes.md").write_text("старый протокол\n", encoding="utf-8")
    assert rt.rebuild(live, CFG) == live
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert "minutes_source_sha256" not in meta, "перештамповка не собирала минутки из этого текста"
    assert "minutes_sha256" in meta


def test_notes_tail_edit_does_not_regenerate(root, monkeypatch):
    import transcript
    speech = "речь\n"
    text = speech + transcript.NOTES_HEAD + "\nправка в ко-мышлении\n"
    live = _meeting(root, text, {"transcript_sha256": _sha("машинный"),
                                 "minutes_source_sha256": _sha(speech)})
    monkeypatch.setattr(rt, "STT", _NoSTT)
    monkeypatch.setattr(rt, "finalize_minutes", lambda *a, **k: pytest.fail("речь не менялась"))
    assert rt.rebuild(live, CFG) == live


def test_force_stt_only_when_one(root, monkeypatch):
    live = _meeting(root, "правленый\n", {"transcript_sha256": _sha("машинный")})
    monkeypatch.setenv("CHAROITE_FORCE_STT", "0")
    assert rt.human_edited_transcript(live, rt.live_meta(live)) is not None


def test_legacy_sidecar_is_adopted_on_write(root):
    """Сайдкар под старым посекундным именем при записи переезжает под своё."""
    live = root / "transcripts" / "2026-09-03_1200_Тема.md"
    live.write_text("текст\n", encoding="utf-8")
    old = root / "transcripts" / "2026-09-03_120005.md.live.json"
    old.write_text(json.dumps({"names": {"Собеседник 1": "Анна"}}), encoding="utf-8")
    assert live_sidecar.remember(live, "transcript_sha256", _sha("текст\n"))
    new = live.with_name(live.name + ".live.json")
    assert new.exists() and not old.exists()
    meta = json.loads(new.read_text(encoding="utf-8"))
    assert meta["names"] == {"Собеседник 1": "Анна"} and meta["transcript_sha256"] == _sha("текст\n")


def test_retitle_without_a_hash_does_not_start_protection(root):
    """Нет хеша — не с чего считать текущие байты машинными: до первой
    настоящей машинной записи защита не включается (advisory DS r3)."""
    bare = "2026-09-03_120005"
    live = root / "transcripts" / f"{bare}.md"
    live.write_text(f"# Встреча {bare}\n\nвозможно, уже правленый текст\n", encoding="utf-8")
    titled = graph_updater.retitle(live, "2026-09-03_1200", bare, "Тема")
    meta = rt.live_meta(titled)
    assert "transcript_sha256" not in meta
    assert rt.human_edited_transcript(titled, meta) is None


def test_owner_of_tells_two_meetings_of_a_minute_apart(root):
    """Сценарий DS r3: A владеет минутой (сайдкар посекундный, до 0.69.1),
    B — посекундная соседка. Владельцы — по штампу, не по счёту."""
    tdir = root / "transcripts"
    a = tdir / "2026-09-03_1200_Отчет.md"; a.write_text("A\n", encoding="utf-8")
    b = tdir / "2026-09-03_120040_Повтор.md"; b.write_text("B\n", encoding="utf-8")
    sa = tdir / "2026-09-03_120005.md.live.json"; sa.write_text("{}", encoding="utf-8")
    sb = tdir / "2026-09-03_120040.md.live.json"; sb.write_text("{}", encoding="utf-8")
    assert live_sidecar.owner_of(sa) == a
    assert live_sidecar.owner_of(sb) == b
    assert live_sidecar.sidecar_for(a) == sa and live_sidecar.sidecar_for(b) == sb
    # усыновление берёт только свой
    assert live_sidecar.remember(b, "transcript_sha256", _sha("B\n"))
    assert sa.exists() and not sb.exists() and b.with_name(b.name + ".live.json").exists()


def test_orphan_sidecar_belongs_to_nobody(root):
    sc = root / "transcripts" / "2026-09-03_120005.md.live.json"; sc.write_text("{}", encoding="utf-8")
    assert live_sidecar.owner_of(sc) is None
    live = root / "transcripts" / "2026-09-03_1201_Другая.md"; live.write_text("x\n", encoding="utf-8")
    assert live_sidecar.sidecar_for(live) == live.with_name(live.name + ".live.json")


def test_titled_sidecar_left_by_an_old_rename_is_found(root):
    """Переименовали до того, как rename_meeting стал переносить сайдкар:
    сайдкар с прежней темой — свой (по имени с темой владельца нет)."""
    tdir = root / "transcripts"
    live = tdir / "2026-09-03_1200_Новая.md"; live.write_text("x\n", encoding="utf-8")
    old = tdir / "2026-09-03_1200_Старая.md.live.json"
    old.write_text(json.dumps({"names": {"Собеседник 1": "Анна"}}), encoding="utf-8")
    assert live_sidecar.owner_of(old) == live, "старая тема без файла — владельца минуты"
    assert live_sidecar.sidecar_for(live) == old
    assert rt.live_meta(live)["names"] == {"Собеседник 1": "Анна"}


def test_neighbour_with_a_service_word_in_the_title_still_owns_its_sidecar(root):
    """«…120030_Разбор.md»: stamp_of даёт None, но это главный файл соседки —
    её сайдкар не наш (Important DS r4 по #489)."""
    tdir = root / "transcripts"
    ours = tdir / "2026-09-03_1200_Синхронизация.md"; ours.write_text("# Встреча\nнаш\n", encoding="utf-8")
    nb = tdir / "2026-09-03_120030_Разбор.md"; nb.write_text("# Встреча 2026-09-03_120030 — Разбор\n", encoding="utf-8")
    sc = tdir / "2026-09-03_120030.md.live.json"
    sc.write_text(json.dumps({"names": {"Собеседник 1": "Инга"}}), encoding="utf-8")
    assert live_sidecar.owner_of(sc) == nb
    assert live_sidecar.sidecar_for(ours) == ours.with_name(ours.name + ".live.json")
    assert rt.live_meta(ours) == {}


def test_retitle_without_a_hash_keeps_the_sidecar_names(root):
    """Боевая форма «нет хеша»: сайдкар после стопа демона с именами, но без
    transcript_sha256 — ретитл переносит его, имена целы, хеш не появляется."""
    bare = "2026-09-03_120005"
    live = root / "transcripts" / f"{bare}.md"
    live.write_text(f"# Встреча {bare}\n\nтекст\n", encoding="utf-8")
    live.with_name(live.name + ".live.json").write_text(
        json.dumps({"names": {"Собеседник 2": "Инга"}, "speakers": 2}), encoding="utf-8")
    titled = graph_updater.retitle(live, "2026-09-03_1200", bare, "Тема")
    meta = rt.live_meta(titled)
    assert meta["names"] == {"Собеседник 2": "Инга"} and "transcript_sha256" not in meta


def test_minute_owner_with_a_service_word_title_is_recognised(root):
    """«…_1200_Демо_live.md» (тема до guard_slug): stamp_of даёт None, но это
    владелец минуты — его посекундный сайдкар свой (Important DS r5 по #489)."""
    tdir = root / "transcripts"
    live = tdir / "2026-09-03_1200_Демо_live.md"
    live.write_text("# Встреча 2026-09-03_1200 — Демо live\n", encoding="utf-8")
    sc = tdir / "2026-09-03_120005.md.live.json"
    sc.write_text(json.dumps({"names": {"Собеседник 1": "Анна"}}), encoding="utf-8")
    assert live_sidecar.owner_of(sc) == live
    assert live_sidecar.sidecar_for(live) == sc
    assert rt.live_meta(live)["names"] == {"Собеседник 1": "Анна"}


def test_per_second_stale_title_sidecar_belongs_to_the_renamed_neighbour(root):
    """Посекундная соседка переименована до переноса пары: её сайдкар под
    старой темой — её (Important DS r7 по #489), а не сирота."""
    tdir = root / "transcripts"
    a = tdir / "2026-09-03_1200_Новая.md"; a.write_text("A\n", encoding="utf-8")
    b = tdir / "2026-09-03_120040_Другое.md"; b.write_text("B\n", encoding="utf-8")
    stale = tdir / "2026-09-03_120040_Повтор.md.live.json"
    stale.write_text(json.dumps({"names": {"Собеседник 1": "Инга"}}), encoding="utf-8")
    assert live_sidecar.owner_of(stale) == b
    assert live_sidecar.sidecar_for(b) == stale
    assert rt.live_meta(b)["names"] == {"Собеседник 1": "Инга"}

