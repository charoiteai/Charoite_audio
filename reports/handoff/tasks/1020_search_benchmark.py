"""Synthetic, model-free graph-search timing. Pass a source checkout as argv[1]."""
import hashlib
import json
import os
import pathlib
import statistics
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve() / 'src'))
from charoite_graph.graph_search import GraphSearch
from charoite_graph.model_seam import Embedder


def refuse_model(*args, **kwargs):
    raise AssertionError('model calls are forbidden in this benchmark')


rows = []
with tempfile.TemporaryDirectory(prefix='charoite-search-bench-') as directory:
    root = pathlib.Path(directory)
    for count in (200, 2000):
        graph = root / str(count)
        graph.mkdir()
        for i in range(count):
            path = graph / f'doc-{i:05d}.md'
            path.write_text(f'# Item {i}\n' + ('alpha status beta delivery ' * (i % 8 + 1)))
            os.utime(path, (1760000000, 1760000000))
        search = GraphSearch(graph, embedder=Embedder(refuse_model, 'none'),
                             data_dir=None, now=lambda: 1760000000)
        search.refresh(force=True)
        search.search('alpha status', semantic=False, dossiers=False)
        elapsed = []
        for _ in range(30):
            start = time.perf_counter_ns()
            result = search.search('alpha status', semantic=False, dossiers=False)
            elapsed.append(time.perf_counter_ns() - start)
        tracemalloc.start()
        search.search('alpha status', semantic=False, dossiers=False)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append({'documents': count, 'median_ms': statistics.median(elapsed) / 1e6,
                     'peak_bytes': peak, 'result_sha256': hashlib.sha256(
                         json.dumps(result.as_dict(), sort_keys=True).encode()).hexdigest()})
print(json.dumps(rows, indent=2))
