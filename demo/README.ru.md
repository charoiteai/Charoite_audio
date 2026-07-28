# Демо-граф — увидеть Чароит до первой встречи

*[English](README.md) · [**Русский**] · [中文](README.zh.md)*

Маленький вымышленный проект («Ромашка», запуск интернет-магазина),
чтобы попробовать вопросы и брифы по архиву, ничего не записывая.

## Попробовать

Наведите `graph_dir` на демо-граф в `config/config.yaml`:

```yaml
sufler:
  graph_dir: /path/to/Charoite_audio/demo/graph
```

Откройте приложение (или CLI) и спросите:

- «что решили по платёжному провайдеру?»
- «какие блокеры сейчас?»
- «подготовь меня к встрече по запуску магазина»

Одна команда проверяет весь RAG-контур на демо-графе (работает даже
до появления `config.yaml`):

```bash
.venv/bin/python scripts/memory_bench.py --demo      # русский демо-граф
.venv/bin/python scripts/memory_bench.py --demo-en   # английский демо-граф
```

Закончили — верните `graph_dir` на свой vault. Всё в `demo/graph` —
вымысел.

## Английское демо

`demo/graph_en` — тот же вымышленный проект по-английски. Наведите на
него `graph_dir`, поставьте `sufler.language: en` и спросите:

- "what did we decide about the payment provider?"
- "what are the current blockers?"
