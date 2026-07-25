# Demo graph — see Charoite before your first meeting

A tiny fictional project («Ромашка», an online shop launch) so you can
try archive questions and briefs without recording anything.

## Try it

Point `graph_dir` at the demo graph in `config/config.yaml`:

```yaml
sufler:
  graph_dir: /path/to/Charoite_audio/demo/graph
```

Open the app (or CLI) and ask:

- «что решили по платёжному провайдеру?»
- «какие блокеры сейчас?»
- «подготовь меня к встрече по запуску магазина»

One command checks the whole RAG loop on the demo graph (works even
before `config.yaml` exists):

```bash
.venv/bin/python scripts/memory_bench.py --demo
```

Switch `graph_dir` back to your real vault when done. Everything in
`demo/graph` is fictional.

## English demo

`demo/graph_en` is the same fictional project in English. Point
`graph_dir` at it, set `sufler.language: en`, and ask:

- "what did we decide about the payment provider?"
- "what are the current blockers?"
