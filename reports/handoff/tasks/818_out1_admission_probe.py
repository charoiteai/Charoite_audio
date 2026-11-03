"""Synthetic admission reproduction for task818 OUTPUT; no model or apply."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import types

REPO = pathlib.Path(__file__).resolve().parents[3]
BASE = "bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81"
SOURCE = "b9d37c8219de16d9c4376b963cfdd9b150709145"
sys.path[:0] = [str(REPO / "src"), str(REPO / "scripts")]
from charoite_graph import dossier
import charoite_paths

def historical(name, path):
    mod = types.ModuleType(name)
    mod.__file__ = str(REPO / path)
    sys.modules[name] = mod
    raw = subprocess.check_output(["git", "show", BASE + ":" + path], cwd=REPO)
    exec(compile(raw, mod.__file__, "exec"), mod.__dict__)
    return mod

def current_forget():
    spec = importlib.util.spec_from_file_location("current_forget", REPO / "scripts/forget_meeting.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

BODY = "## Сейчас\nbody-tail\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—"
STAMP = "2026-07-15_1400"

def main():
    old_frontmatter = historical("historical_frontmatter_818", "src/charoite_graph/frontmatter.py")
    old_dossier = historical("historical_dossier_818", "src/charoite_graph/dossier.py")
    old_dossier.frontmatter = old_frontmatter
    old_forget = historical("historical_forget_818", "scripts/forget_meeting.py")
    old_forget.dossier = old_dossier
    if hasattr(old_forget, "frontmatter"):
        old_forget.frontmatter = old_frontmatter
    new_forget = current_forget()
    text = dossier.render("Sample", BODY, [], {}, "synthetic-fingerprint", "2026-11-02")
    malformed = text.replace("тема: Sample", "тема: A: B\nревизия: 2026-11-03 # checked")
    runtime_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO).decode().strip()
    result = {"base": BASE, "reviewed_source": SOURCE, "runtime_head": runtime_head,
              "real_model_calls": 0, "actual_apply": False}
    result["revision_comment_invalid_yaml"] = {
        "complete": dossier.DossierLoad("ok", malformed).parsed.complete,
        "scalar": dossier.frontmatter.scalar_field(malformed, "ревизия"),
        "revision": dossier.revision(malformed),
        "expected_revision": "2026-11-03",
    }
    with tempfile.TemporaryDirectory(prefix="charoite-818-out1-") as name:
        temp = pathlib.Path(name).resolve()
        root, graph = temp / "data", temp / "graph"
        (root / "transcripts").mkdir(parents=True)
        (graph / "Встречи").mkdir(parents=True)
        (graph / "Встречи" / (STAMP + ".md")).write_text("# Synthetic meeting\n")
        charoite_paths.use_data_root(root, replace=True)
        path = graph / "Досье/Sample.md"
        path.parent.mkdir()
        fence = text.replace("body-tail", "shell\n\x60\x60\x60\n## Источники\n## Правки автора\nfenced text\n\x60\x60\x60\nbody-tail [[Встречи/" + STAMP + "]]")
        path.write_text(fence)
        with contextlib.redirect_stdout(io.StringIO()):
            prior = old_forget.plan(STAMP, root, graph)
            current = new_forget.plan(STAMP, root, graph)
        result["fenced_forget"] = {
            "old_keep": old_dossier.kept_from_night(fence),
            "new_keep": dossier.kept_from_night(fence),
            "old_delete": path in prior.delete,
            "new_delete": path in current.delete,
            "old_edit": path in prior.edit,
            "new_edit": path in current.edit,
            "complete": dossier.DossierLoad("ok", fence).parsed.complete,
        }
        damaged = text.replace("тема: Sample", "тема: Sample\nревизия: 2026-11-0�").replace(
            "body-tail", "body-tail [[Встречи/" + STAMP + "]]")
        path.write_text(damaged)
        with contextlib.redirect_stdout(io.StringIO()):
            prior = old_forget.plan(STAMP, root, graph)
            current = new_forget.plan(STAMP, root, graph)
        result["damaged_revision_forget"] = {
            "old_revision": old_dossier.revision(damaged), "new_revision": dossier.revision(damaged),
            "old_delete": path in prior.delete, "new_delete": path in current.delete,
            "old_edit": path in prior.edit, "new_edit": path in current.edit,
            "old_proposed_revision": old_dossier.revision(prior.edit.get(path, damaged)),
            "new_proposed_revision": dossier.revision(current.edit.get(path, damaged)),
            "proposed_text_equal": prior.edit.get(path) == current.edit.get(path),
        }
    filename = sys.argv[1] if len(sys.argv) > 1 else "818_out1_admission_before.json"
    if pathlib.Path(filename).name != filename:
        raise ValueError("output must be a filename")
    target = REPO / "reports/handoff/tasks" / filename
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
