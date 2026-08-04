import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meeting_archive import _write_manifest, build_manifest  # noqa: E402


def test_manifest_is_language_independent_index_of_markdown(tmp_path):
    folder = tmp_path / "2026-08-03 11-30 — Планирование"
    folder.mkdir()
    (folder / "Стенограмма.md").write_text(
        "Участники: Иван, Мария\n\n"
        "**Иван** [11:30]: начали\n"
        "**Мария** [12:05]: закончили\n",
        encoding="utf-8",
    )
    (folder / "Саммари.md").write_text(
        "**Суть одной строкой:** План согласован\n\n"
        "## Решили\n- Выпустить в пятницу\n\n"
        "## Поручения\n- **Мария** — проверить сборку\n\n"
        "## Открытые вопросы\n- Нужен ли Android\n",
        encoding="utf-8",
    )

    manifest = build_manifest(folder, "2026-08-03_1130", "Планирование")

    assert manifest["schema_version"] == 1
    assert manifest["meeting_id"] == "2026-08-03_1130"
    assert manifest["started_at"] == "2026-08-03T11:30:00"
    assert manifest["duration_minutes"] == 35
    assert manifest["participants"] == ["Иван", "Мария"]
    assert manifest["summary"] == "План согласован"
    assert manifest["decisions"] == ["Выпустить в пятницу"]
    assert manifest["action_items"] == ["**Мария** — проверить сборку"]
    assert manifest["files"] == {"transcript": "Стенограмма.md", "summary": "Саммари.md"}


def test_manifest_write_is_valid_json(tmp_path):
    folder = tmp_path / "meeting"
    folder.mkdir()
    _write_manifest(folder, "2026-08-03_1130", "Тема")
    saved = json.loads((folder / "meeting.meta.json").read_text(encoding="utf-8"))
    assert saved["title"] == "Тема"
    assert saved["files"] == {}
