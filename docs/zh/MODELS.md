# 为什么选择这些模型

*[English](../MODELS.md) · [Русский](../ru/MODELS.md) · **中文***

一切都在本地运行。以下是每个默认选择的依据:我们在 M1 Max(32 GB)上的
基准测试,加上独立来源。每个选择都可以在配置中替换。

## STT:GigaAM v3(默认)

`gigaam-v3-e2e-rnnt`,通过 [onnx_asr](https://github.com/istupakov/onnx-asr) 运行 —
由 [Sber](https://github.com/salute-developers/GigaAM) 开发的俄语 ASR 模型,MIT 许可。

- **速度**:3 秒的音频块在 M1 Max 上约 0.1–0.6 秒完成转写 — 实时逐字稿的
  延迟主要由 STT 决定,这留出了充足余量。
- **俄语质量**:在真实会议上明显比 whisper-large-v3-turbo 更准确 —
  短音频块上的幻觉更少,对领域术语和缩写更稳健。
- **内置标点和大小写**(e2e 模型)— 这一点至关重要:句尾的 “?” 是触发
  即时回答的主要信号,并且逐字稿无需处理即可直接阅读。
- 模型在首次运行时自动下载。

配置中的替代选项:`whisper`(mlx,支持 100+ 种语言)和 `parakeet`
(英语,速度极快),用于非俄语会议。

## 主 LLM:qwen3.6:35b-a3b

MoE 架构:总参数约 35B,激活参数约 3B — 以小模型的速度提供 30B 级的质量。

- **我们在真实会议逐字稿上的基准测试**:首个 token 0.27 秒,完整回答
  2.2 秒 — 对比 dense 模型 gemma4:26b 的 1.08 秒 / 4.5 秒,同时更稳定地
  保持助手角色(gemma 会混淆发言人)。
- **生成速度**（M1 Max、32 GB、`num_ctx: 8192`，2026-08-08 两次相同条件实测）：
  `qwen3.6:35b-a3b` 约 27 tok/s，轻量的 `qwen3.5:4b` 约 32 tok/s。说话速度约为
  每秒 4-5 个 token，因此两者都跑在对话前面——重要的是每条提示的速度，而不是
  每小时的吞吐。
- **30B 级是结构化/图谱抽取的下限** — 这不是我们的偏好,而是行业观察:
  [LightRAG](https://github.com/hkuds/lightrag) 将 Qwen3-30B-A3B 列为
  实体抽取的合理下限;
  [Graphiti](https://github.com/getzep/graphiti) 警告过小的模型会破坏
  JSON schema;在 schema 引导的 KG 基准测试
  [OSKGC](https://ceur-ws.org/Vol-4041/paper1.pdf) 中,7–8B 模型比前沿
  模型低约 0.1 Micro F1,且在本体合规性上问题最大。Charoite 要构建
  知识图谱,因此低于 30B 级不可行。
- 所有调用均 `think: false`:推理模式会把输出移入 thinking 字段
  (content 为空),并增加约 10 秒延迟。

## 轻量模型:qwen3.5:4b

实时要点、分类、会议纪要草稿 — 所有需要每隔几秒与主模型并行运行的任务。

- **我们对比 gemma4:e4b 的基准测试**(2026 年 7 月,真实助手任务):
  问题分类更准确(e4b 答错了一个直接提问),要点生成 2.9 秒对 3.3 秒
  且没有客套开场白,内存 3.4 GB 对 9.6 GB — 与主模型并行时轻了近 3 倍。
- 例外是**对话标注**(`markup_model: gemma4:latest`):那里要求逐字保留
  原文,而 qwen3.5:4b 倾向于轻微润色;gemma 能保持文本原样。
- 内存非常有限时 — `qwen3.5:2b`(同家族的 edge 级模型)。

## 说话人分离:ERes2Net(3D-Speaker)

说话人嵌入 — [ERes2Net](https://github.com/modelscope/3D-Speaker)
(ONNX,512 维)。

- **我们在真实会议录音上的基准测试**,对比 CAM++ 和 TitaNet:ERes2Net
  区分相同/不同说话人的能力最强 — 同一说话人余弦相似度 0.29–0.8,
  通话通道上跨说话人 ≤0.16,由此得到可用的阈值(0.45 加相对的说话人
  切换规则)。
- 市场背景:即使是最好的开源管线 pyannote 3.1,在会议(AMI)上的 DER
  也约为 19%,且以录音中途标签互换著称 — 因此 Charoite 在实时说话人
  分离之外,还对完整录音做离线重跑(回声过滤、微片段合并、姓名分配)。

## 必须设置 num_ctx: 8192

部分 Ollama Modelfile 的默认上下文是 262144 — 如果不显式设置
`num_ctx`,KV cache 会膨胀数 GB,生成速度下降数倍。Charoite 的每次
调用都显式传入 `num_ctx: 8192`。

## 英文会议

默认 STT 面向俄语。面向英语用户:

- **Parakeet TDT 0.6B v3**(`stt.backend: parakeet`)— Open ASR
  Leaderboard 上 6.32% WER,对比 Whisper 的 7.44%,速度可达实时的
  数千倍;配置中已支持。
- **Moonshine** — 原生流式(边说边出词,延迟约 107 ms,模型最小
  27 MB)— 可替代服务端流式 STT、用于提前检测提问的候选方案。
- `whisper-large-v3-turbo` — 多语言后备方案(100+ 种语言)。

中文会议使用 whisper-large-v3-turbo(`stt.backend: whisper`,
`language: zh`),而主 LLM Qwen 本身原生支持中文。

## 手机(路线图)

内存预算:6 GB 的手机实际能给模型约 3–3.5 GB。可行的移动端组合:
**Moonshine Tiny/Base**(27–245 MB,CPU)或基于 ANE 的 ASR,加
**qwen3.5:0.8b/2b**(手机上约 25–40 tok/s)用于要点和摘要。iOS 上
还有:内置的约 3B Foundation Models(iOS 26+,零下载)和用于原生
Swift 推理的 Core AI;说话人分离通过 ANE 管线。模型选择仍在配置中。

## 按 RAM 划分的预设 — macOS

实时提示和图谱 LLM 是吃内存的部分;STT(约 1 GB)和说话人分离
(约 0.5 GB)恒定。数字为 Apple Silicon 上的工作集,全程
`num_ctx: 8192`(必须 — 更大的上下文会重新加载模型并撑爆内存)。

| RAM | 主 LLM | 轻量 LLM | STT | 能得到什么 |
|----|----|----|----|----|
| **4 GB** | — | — | GigaAM | 不够运行本地 LLM。只运行 STT(实时逐字稿 + 保存会议纪要)。提示可以交给你自己另一台机器上的 Ollama——但逐字稿会发往那里，因此配置中必须显式写上 `llm.allow_remote: true`，且在 `CHAROITE_NO_CLOUD` 下会被拒绝（见 PRIVACY.zh.md）。 |
| **8 GB** | `qwen3.5:4b` (3.4 GB) | 同一模型 | GigaAM | 逐字稿、要点、会议纪要草稿、基础提示。一个模型兼任两个角色;无并行 Claude 层。跳过图谱(30B 下限)。 |
| **16 GB** | `gemma4:latest` (9.6 GB) | `qwen3.5:2b` | GigaAM | 完整实时循环:提示 + 要点 + 会议纪要并行。图谱抽取可用但更慢。推荐的入门配置。 |
| **32 GB** | `qwen3.6:35b-a3b` (23 GB) | `qwen3.5:4b` (3.4 GB) | GigaAM | 默认配置。大模型提示,轻量模型并行生成要点,图谱抽取可靠。基准测试即在此配置上进行。 |
| **64 GB+** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | GigaAM | 模型相同,但有余量运行可选的云端 Claude 层、更长的会议,以及离线重建逐字稿而不挤掉模型。 |

经验法则:16 GB 以下放弃知识图谱 — 30B 以下的模型会破坏 JSON
schema。8 GB 以下只在本地保留 STT。`small_model` 始终与主模型并行
运行,因此要同时为两者预留内存。

## 按 RAM 划分的预设 — iOS / iPadOS

手机和平板装不下 30B 模型,所以分工不同:设备负责 STT 和轻量生成,
更重的任务通过 REST API(`llm.base_url`)交给 Mac。iOS 还对单个应用
的内存设上限(约为物理内存的一半),因此可用预算小于标称数字。

| 设备 RAM | 本地 | 通过 REST API | 备注 |
|----|----|----|----|
| **4 GB**(较旧的 iPhone/iPad) | 仅 STT(Moonshine Tiny,ANE) | 提示、要点、图谱 | 瘦客户端。逐字稿在设备上实时生成,智能功能全部来自 Mac。 |
| **6 GB**(iPhone 15/16 标准版) | STT + `qwen3.5:0.8b` 生成要点 | 提示、会议纪要、图谱 | 设备端要点和快速回答;深度任务交给 Mac。 |
| **8 GB**(iPhone Pro、iPad) | STT + `qwen3.5:2b` | 图谱、云层 | 实时循环大部分在本地运行;只有图谱需要 Mac。 |
| **iOS 26+**(任何设备) | + 内置约 3B Foundation Models | 图谱 | Apple 的设备端模型通过 Core AI 免费提供(零下载)— 用于要点/分类,图谱留在 Mac 上。 |

移动端 STT 选择 **Moonshine**(原生流式,27–245 MB,延迟约 107 ms)
而非为 Mac 调优的 GigaAM。iOS 上的说话人分离通过 ANE 管线运行。
这一切都保持可配置 — 手机是客户端,只要能连上 Mac,就可以借用
Mac 的模型。

## 云端模型（当该层开启时）

云层默认完全关闭——哪个开关启用什么，见 [PRIVACY.zh.md](PRIVACY.md)。本节
只谈模型选择。

| 配置键 | 默认值 | 在哪里运行 |
|----|----|----|
| `cloud_model` | `claude-opus-5` | 会后复盘、每夜的核心与档案修订——不在对话速度下运行，因此值得用最强的模型 |
| `cloud_live_model` | `claude-haiku-4-5` | 会议进行中回答问题：速度更重要 |
| `cloud_hints_model` | `claude-haiku-4-5` | 提示润色：同理，但更频繁 |

默认值只写在一个地方——`src/cloud.py`——并与示例配置保持一致；不一致会导致
测试失败。此前这些字面量散落在每个调用点，其中一个键（`cloud_model`）有两个
不同的默认值：配置被精简后，会后复盘与夜间修订会走向不同的模型。

## 更换模型

所有设置都在 `config/config.yaml` 中:`stt.backend`、`llm.model`、
`llm.small_model`;嵌入模型就是文件 `models/diar/embedding.onnx`。
16 GB 的机器建议从 `llm.model: gemma4:latest` 和更轻的 STT 后端开始。
