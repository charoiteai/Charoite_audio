# 演示图谱 — 在第一场会议之前看看 Charoite

*[English](../../../demo/README.md) · [Русский](../../ru/demo/README.md) · [**中文**]*

一个微型虚构项目（«Ромашка»，网店上线），让您无需录制任何内容即可
体验档案问答和简报。

## 试一试

在 `config/config.yaml` 中把 `graph_dir` 指向演示图谱：

```yaml
sufler:
  graph_dir: /path/to/Charoite_audio/demo/graph
```

打开应用（或命令行），提问（俄语演示图谱）：

- «что решили по платёжному провайдеру?»
- «какие блокеры сейчас?»
- «подготовь меня к встрече по запуску магазина»

一条命令即可在演示图谱上验证整个 RAG 闭环（`config.yaml` 尚未创建也能跑）：

```bash
.venv/bin/python scripts/memory_bench.py --demo      # 俄语演示图谱
.venv/bin/python scripts/memory_bench.py --demo-en   # 英语演示图谱
.venv/bin/python scripts/memory_bench.py --demo-zh   # 中文演示图谱
```

体验完把 `graph_dir` 换回您真实的 vault。`demo/graph` 里的一切都是虚构的。

## 英语演示

`demo/graph_en` 是同一虚构项目的英文版。把 `graph_dir` 指向它，
设置 `sufler.language: en`，然后提问：

- "what did we decide about the payment provider?"
- "what are the current blockers?"

## 中文演示

`demo/graph_zh` 是同一虚构项目的中文版，团队成员也是中文名。把
`graph_dir` 指向它，设置 `sufler.language: zh`，然后提问：

- 支付服务商最后定了哪一家？
- 现在有哪些阻碍？
- 网店计划什么时候上线？
