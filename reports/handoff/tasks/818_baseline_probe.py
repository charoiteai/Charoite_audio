"""Synthetic parser baseline; no owner config, graph, model or pipeline write."""
from pathlib import Path
import importlib
import json
import subprocess
import sys

BASE = "bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81"
root = Path(__file__).resolve().parents[3]
manifest = json.loads((root / "docs/design/818-source-contract-manifest.json").read_text())
assert manifest["base_sha"] == BASE
for source in manifest["source_blobs"]:
    actual = subprocess.check_output(
        ["git", "hash-object", source["path"]], cwd=root, text=True,
    ).strip()
    assert actual == source["git_blob"], source["path"]
sys.path.insert(0, str(root / "src"))
dossier = importlib.import_module("charoite_graph.dossier")

body = ("## Сейчас\n- discussed ##Правки автора\nbody-tail\n## Как пришли\n—\n"
        "## Решено\n—\n## Открыто\n—\n## Кто в теме\n—")
text = dossier.render("Sample", body, [], {}, "synthetic-fingerprint", "2026-11-02")
text = text.replace("## Правки автора\n\n—\n", "## Правки автора\n\nowner-note\n")
lengths = [len(text)]
for _ in range(2):
    manual = dossier.preserve_manual(text)
    text = dossier.render("Sample", body, [], {}, "synthetic-fingerprint", "2026-11-02")
    text = text.replace("## Правки автора\n\n—\n", "## Правки автора\n\n" + manual + "\n")
    lengths.append(len(text))
legacy = "##Правки автора\nfirst-note\n- discussed ## Правки автора\nsecond-note\n"
manual = dossier.preserve_manual(legacy)
ambiguous = ("# Dossier\n## Сейчас\nintro\n## Источники\nbody-tail\n"
             "## Как пришли\nmore-body\n## Правки автора\nowner-note\n")
parsed = dossier.split_dossier(ambiguous)
result = {
    "base_sha": BASE,
    "synthetic_only": True,
    "model_calls": 0,
    "inline_growth_lengths": lengths,
    "inline_growth_reproduced": lengths[0] < lengths[1] < lengths[2],
    "unspaced_manual_kept_both": "first-note" in manual and "second-note" in manual,
    "bare_heading_body_tail_present": "body-tail" in parsed[1],
    "implementation_changed": False,
}
assert result["inline_growth_reproduced"]
assert result["unspaced_manual_kept_both"]
assert not result["bare_heading_body_tail_present"]
revision_text = "---\nревизия: 2026-10-3\ufffd\n---\nbody\n"
result["damaged_revision_scalar_still_held"] = bool(dossier.revision(revision_text))
result["literal_author_replacement_is_not_decode_damage"] = (
    dossier.preserve_manual("## Правки автора\nauthor \ufffd\n") == "author \ufffd"
)
result["probe_kind"] = "three synthetic parser baseline cases plus scalar observations; no pipeline write"
assert result["damaged_revision_scalar_still_held"]
assert result["literal_author_replacement_is_not_decode_damage"]
(root / "docs/design/dossier-parser-baseline-bcf.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
)
print(json.dumps(result, ensure_ascii=False))
