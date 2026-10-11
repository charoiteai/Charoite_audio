import hashlib
import pytest
from scripts import decision_evidence_audit as de


def record(key="d1", **kw):
    return {"id": key, "kind": "decision", "date": "2026-10-01", "source": "One.md",
            "quote": "Бюджет не согласован", **kw}


def test_quote_check_preserves_negation_and_does_not_claim_semantic_verification(tmp_path):
    (tmp_path / "One.md").write_text("Бюджет\nне согласован", encoding="utf-8")
    result = de.audit(tmp_path, [record()])[0]
    assert result["quote_present"] and not result["findings"]
    assert result["semantic_support"] == "not_checked"
    assert "current" not in result
    assert "quote_not_found" in de.audit(tmp_path, [record(quote="Бюджет согласован")])[0]["findings"]


def test_changed_source_has_fingerprint_and_missing_quote_is_reported(tmp_path):
    raw = "Обсуждение перенесено".encode()
    (tmp_path / "One.md").write_bytes(raw)
    result = de.audit(tmp_path, [record(source_sha256="old")])[0]
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert set(result["findings"]) == {"source_changed", "quote_not_found"}


def test_unsafe_path_and_external_symlink_are_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.md")
    outside.write_text("Бюджет не согласован", encoding="utf-8")
    try:
        (tmp_path / "One.md").symlink_to(outside)
        assert "outside_graph" in de.audit(tmp_path, [record()])[0]["findings"]
        for path in ("../outside.md", "/outside.md", ".backup/One.md", "One.txt"):
            assert "unsafe_source" in de.audit(tmp_path, [record(source=path)])[0]["findings"]
    finally:
        outside.unlink()


def test_broken_utf8_and_missing_evidence_are_not_accepted(tmp_path):
    (tmp_path / "One.md").write_bytes(b"\xff")
    assert "source_not_utf8" in de.audit(tmp_path, [record()])[0]["findings"]
    assert "missing_evidence" in de.audit(tmp_path, [record(quote=" ")])[0]["findings"]


def test_cycle_backdated_replacement_and_proposal_are_separate_findings(tmp_path):
    (tmp_path / "One.md").write_text("Бюджет не согласован", encoding="utf-8")
    results = de.audit(tmp_path, [record("a", supersedes="b", kind="proposal"),
                                  record("b", supersedes="a", date="2026-10-02")])
    assert "supersession_cycle" in results[0]["findings"]
    assert "supersession_reverses_time" in results[0]["findings"]
    assert "supersession_requires_decisions" in results[0]["findings"]


def test_missing_predecessor_and_invalid_date_do_not_crash(tmp_path):
    (tmp_path / "One.md").write_text("Бюджет не согласован", encoding="utf-8")
    findings = de.audit(tmp_path, [record(date="2026-13-40", supersedes="missing")])[0]["findings"]
    assert set(findings) == {"invalid_date", "superseded_record_missing"}


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        de.audit(tmp_path, [record(), record()])
