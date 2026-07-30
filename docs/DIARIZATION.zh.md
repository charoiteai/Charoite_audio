# 说话人分离配置

*[English](DIARIZATION.md) · [Русский](DIARIZATION.ru.md) · **中文***

Charoite 采用两遍说话人分离：

1. **实时**（会议进行中）：说话人嵌入模型实时把音频块标注为 «Собеседник 1/2/…»（“对话者 1/2/…”）。需要 ONNX 格式的 ERes2Net 嵌入模型，放在 `models/diar/embedding.onnx`（512 维输出，16 kHz 输入）。可从 [3D-Speaker 项目](https://github.com/modelscope/3D-Speaker)获取（在 CN-Celeb/VoxCeleb 上训练的 ERes2Net 对俄语和英语效果良好），一条命令即可安装：

   ```bash
   .venv/bin/python scripts/get_models.py --diar          # 默认模型
   .venv/bin/python scripts/get_models.py --diar --list   # 还有哪些可选
   .venv/bin/python scripts/get_models.py --diar --check  # 检查已安装的模型
   ```

   脚本会在连接前打印地址，校验下载的确实是 ONNX 且没有被截断，并放到守护进程查找的位置。模型来自 [3D-Speaker 项目](https://github.com/modelscope/3D-Speaker)（Apache-2.0），ONNX 镜像为 sherpa-onnx 整理。自定义链接用 `--url`。

   除可选云层之外，这是产品中唯一会联网的地方：只在你手动运行时联网，一次，之后完全离线；`--check` 完全不开连接。
2. **离线重跑**（按下停止后）：对完整录音按声道重新做说话人分离，过滤麦克风与系统音频之间的回声，把微小片段并入相邻片段，并由本地 LLM 根据对话中听到的名字给说话人命名。结果会替换实时生成的逐字稿草稿。

没有 `models/diar/embedding.onnx` 时 Charoite 仍能工作：改用声道标签（你 vs. 对方）代替按声音区分的标签。

调优（`config/config.yaml`）：

- `live_diarize_threshold`（默认 0.45）— 把音频块归到已知声音的余弦相似度阈值；不同人被合并成一个时调高，同一个人总被拆成两个时调低。
