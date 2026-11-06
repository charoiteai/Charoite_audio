# Independent final risk review — 954/958

Snapshot: 6b317c0138269468681cda1fc8c6a359749a5241; diagnostic base: 2f706a328bc7b880cd6c694f7786ca9b12b7de41. Static review only. All 450 listed files passed SHA-256 verification; manifest digest without trailing LF: ae827cf0d11bed487d29ef7d6495b65edd58a766275c81b9e2476cd89e8230b4. Prior review verdicts were not consulted.

## Important

- `scripts/nightly_dossier.py:245`, `scripts/nightly_dossier.py:746`, `scripts/nightly_dossier.py:793`: an unexpected exception during an index publication initiated by `g.done()` is omitted from `index_failed`. `write_index()` sets `indexed = True` before the operation. If e.g. `dossier.rebuild_index()` raises `RuntimeError`, the exception escapes `g.done()`, the cleanup call sees `indexed` and returns, and neither the cleanup exception counter nor the graph's `index_failed` flag is set. The aggregate reports `index_failed: 0` despite an interrupted failed index publication. Count exceptions at the common publication boundary exactly once, preserving propagation and existing index behavior; add a synthetic test where the final queued topic triggers `g.done()` and the index operation raises an unexpected exception.

## Critical / Minor

No additional findings from static review.

The aggregate projects enum keys and counters rather than input text; adapter entries do not establish HTTP delivery. Existing threshold and combined scan-failure exit remain in place in the reviewed code. The reported targeted tests and mutation replays were not rerun or independently verified. The full CI for this head remains required; this review is not a CI or real-night verdict, and the original incident cause remains unknown.
