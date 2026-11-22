# 说话人分离（Diarization）

*[English](../DIARIZATION.md) · [Русский](../ru/DIARIZATION.md) · **中文***

本文说明当前 `main` 分支上的说话人分离架构：包含哪些引擎、各自如何开启、音频如何流转、模块间契约、阈值位置与已知限制。阈值来源和实验记录见 [research/diarization-log.md](../research/diarization-log.md)。

说话人分离只回答一个问题：*这段语音属于哪个声音*。谁是机主、该给声音起什么名字由上层独立规则决定（第 5、6 节）。即使没有安装分离模型，Charoite 也能正常工作：逐字稿按声道署名（麦克风为机主，系统声道为对方）。

## 1. 三套引擎栈

| 引擎栈 | 运行时机 | 代码位置 | 模型 | 作用 |
|---|---|---|---|---|
| **实时追踪器**（ERes2Net） | 会议进行中 | `src/diarize_live.py`（`SegmentTracker` / 无分段时的 `SpeakerTracker`） | `models/diar/embedding.onnx`, `models/diar/segmentation.onnx` | 双声道实时声音编号、回声检测、流式回退布局 |
| **sherpa-onnx 离线重跑** | 停止录音后 | `src/diarize.py`（`diarize`, `merge_shards`）；`src/sherpa_config.py` | 同上两文件 | 按声道生成最终逐字稿说话人标签；Nemotron 关闭或拒绝时的后备引擎 |
| **Nemotron 3 Diarization**（MLX） | 会后，及实时 `shadow`/`on` | `src/diarize_nemotron.py`、`src/live_nemotron.py` | `models/diar/nemotron/` + 独立环境 `engines/nemotron` | 会后标注远端和麦克风声道；`on` 模式下实时标注远端声道 |

即使开启实时 Nemotron，也不能移除实时追踪器：`live_nemotron.start` 依赖分段追踪器来判断回声和机主归属。Nemotron 始终通过 `foreign_python` 在独立子进程中运行，MLX 和 `transformers` 不会进入主进程。

## 2. 开关配置与模型安装

所有配置键位于 `config/config.yaml` 的 `sufler` 段：

| 配置键 | 取值（默认） | 读取方 |
|---|---|---|
| `live_diarize` | `true` / `false`（`true`） | `daemon.py` |
| `live_diarize_threshold` | 余弦阈值（`0.45`） | `SpeakerTracker`（仅简化模式） |
| `diarize_backend` | `sherpa` / `nemotron`（`sherpa`） | `rebuild_transcript`、`diarize_nemotron.engine_state` |
| `live_nemotron` | `off` / `shadow` / `on`（`off`） | `live_nemotron.start`、`engine_state` |
| `nemotron_python` | 路径或空字符串（`""` = 内置安装环境） | `diarize_nemotron.engine_interpreter` |

### 安装模型

```bash
.venv/bin/python scripts/get_models.py --diar          # 嵌入模型 + 分段模型
.venv/bin/python scripts/get_models.py --segmentation  # 仅分段模型
.venv/bin/python scripts/get_models.py --diar --check  # 离线校验两个文件
```

安装独立 Nemotron 引擎环境：

```bash
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron --check
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron --plan
```

退出码：`0` 成功，`1` 拒绝，`12`（`EXIT_INSTALL_BUSY`）机器正忙，`13`（`EXIT_INSTALL_CANCELLED`）已取消。引擎状态由 `diarize_nemotron.engine_state(root, cfg)` 统一计算。

## 会后使用 Nemotron

```yaml
diarize_backend: nemotron   # 默认 sherpa
nemotron_python: ""
```

任何拒绝或失败都会按声道回退到 sherpa，并在逐字稿头部记录原因（`ENGINE_REFUSED_REASON`）。开发环境的依赖锁定版本为 `MLX_AUDIO_VERSION`（`src/diarize_nemotron.py`）：

```bash
.venv/bin/pip install "mlx-audio==0.5.9"
.venv/bin/hf download mlx-community/Nemotron-3-Diarization --local-dir models/diar/nemotron
```

`load_model` 只接受包含 `config.json` 与权重的本地目录。声音缓存仅存内存、随进程销毁（`tests/test_no_voice_biometrics.py`）。权重遵循 NVIDIA OpenMDW 1.1，不提交入库。

## 3. 数据流

- **单声道统一采样轴**：`audio.py` 对每个进入声道的采样（含缓冲区截断部分）统一计数，使 STT 分块与实时流分段精确对齐。
- **让位给识别**：当 STT 积压 ≥ 2 块且 ≥ 6 秒时，暂停实时分离，会后重跑再重新标注。
- **让位给实时会议**：会后重跑在每个重型声道任务前调用 `_yield_to_live`，新会议开始时立即让出资源。

## 4. 引擎间契约

- 短于 `MIN_SEGMENT_S`（1 秒）的片段会被丢弃。
- 声道长度 ≤ 20 秒返回 `None` 时保留实时人数；超过 20 秒返回 `None` 视为引擎失败，保留实时逐字稿；返回 `[]` 视为静默（Nemotron 在达到通话阈值后返回 `[]` 视为拒绝并回退 sherpa）。
- 仅当远端声道由 Nemotron 标注（或无远端录音且选择 `nemotron`）时，麦克风才送入 Nemotron。
- 实时流（`on`）单个分块最多等待 `WAIT_CAP_S`（2 秒），超时则回退追踪器布局；流标签从 `STREAM_VOICE_BASE`（1000）起编号，避免与追踪器冲突。日志写入 `logs/nemotron_live_<stamp>.jsonl`（`JOURNAL_V = 3`，仅含数值和原因）。

## 5. 机主与回声规则（`src/owner_voice.py`）

- 麦克风标签取自 `sufler.user_name`（为空时取默认标签）。
- 通话期间（120 秒窗口内系统声道语音 ≥ 30 秒触发通话标志），麦克风上所有非回声语音（累计 ≥ 15 秒）均归属机主。
- 被远端声道覆盖超过一半的麦克风片段作为回声丢弃；当远端声道缺失或连续静默 ≥ 60 秒时，已证为回声的声音改为中性标签。

## 6. 姓名与音高（`src/speaker_names.py`、`src/voice_pitch.py`）

- 本地 LLM 结合可信规则为标签匹配对话中出现的姓名；拒绝结果在本次会议内记住。
- 基频 F0（`LOW_MAX` 145 Hz，`HIGH_MIN` 190 Hz）仅用于否决与音色明显矛盾的姓名，仅存内存不落盘。

## 7. 关键阈值

| 阈值 | 数值 | 位置 |
|---|---|---|
| 分段模式追踪器附着 | 0.62 | `diarize_live.SegmentTracker` |
| 简化模式追踪器附着 | 0.45 | `sufler.live_diarize_threshold` |
| 单声道实时声音上限 | 8 | `diarize_live.LIVE_MAX_SPEAKERS` |
| sherpa 聚类 / 碎片合并 / 会议室否决 | 0.8 / 0.60 / 0.46 | `diarize.py` |
| 最小参与时长 | 30 s | `diarize.MIN_SPEAKER_S` |
| 矮簇阈值（远端 / 麦克风） | 25 s（sherpa）、5 s（Nemotron）/ 10 s | `rebuild_transcript.py` |
| 实时流展示 / 队列 / 内存停止 / MLX 缓存 | 5 s / 10 s / 2048 MB / 512 MB | `live_nemotron.py` |

## 8. 已知限制

- 通话时同一麦克风旁的同事会被归为机主（按声道而非声纹判断）。
- 对方全程静默的通话会被当作线下会议室处理。
- 会议室里外放超过 30 秒的音频会触发通话标志。
- 超过 8 位发言人时超出部分仅按声道标注（Nemotron 共 8 槽位）。
- 合成语音会误导 Nemotron；请在真实录音上评估。俄语不在 Nemotron 训练语言内。

## 9. 评测与交叉发言实验

使用 `scripts/diar_bench.py` 计算 DER，使用 `scripts/nemotron_shadow_replay.py` 与 `scripts/nemotron_shadow_check.py` 回放评估实时流：

```bash
.venv/bin/python scripts/diar_bench.py --make
.venv/bin/python scripts/diar_bench.py --wav recordings/<stamp>_blackhole.wav --engine compare
```

在 M1 Max 上对 AMI（Mix-Headset，4 说话人，严格词级参考）的实测：生产 `diarize.diarize` DER 为 0.606 / 0.453（RTF 0.35）；Nemotron 整文件为 **0.269** / **0.277**（RTF 0.003）；Nemotron 实时流为 0.273 / 0.273（RTF 0.04）。完整记录见 [research/diarization-log.md](../research/diarization-log.md)。
