# 脚本

*[English](../../../scripts/README.md) · [Русский](../../ru/scripts/README.md) · [**中文**]*

运维辅助脚本。全部本地，全部可选。

有依赖的脚本应通过 `.venv/bin/python` 运行；`doctor.py` 是特意设计的例外，
安装前也能用系统 `python3` 运行。数据根目录（录音、逐字稿、日志）为
`CHAROITE_ROOT`，未设置时即仓库本身。`import_meeting.py` 要求明确指定：
没有 `CHAROITE_ROOT` 时它以退出码 5 拒绝并打印用法，因此在克隆中应这样运行：
`CHAROITE_ROOT="$PWD" .venv/bin/python scripts/import_meeting.py …`。
任务流程与恢复顺序见[用户实用指南](../USER_GUIDE.md)和[数据地图](../DATA_AND_RECOVERY.md)。

## 日常

- `doctor.py` — 一条命令：缺什么以及怎么修。安装部分（配置及其键、图谱文件夹、Ollama 与所需模型（含 bge-m3）、STT 模型、说话人分离、依赖）和运行部分（真实的生成探测、卡住的会议、导入队列、磁盘空间）。它自己什么都不修。`--restart-llm` 是越过活跃租约的模型服务器紧急重启——端口持有者卡死时唯一的手动出口。
- `import_meeting.py` — 把已录制的会议（音频 / 文本 / Zoom 或 Teams 字幕）导入档案和图谱；`note_`/`diary_` 音频进入笔记流水线。`--date`/`--time`/`--title` 覆盖录音自身携带的信息。`--scan <文件夹>` 导入整个导入文件夹：成功的文件连同 sidecar 移入 `done/`，失败的带标记留下，直到 `--retry-failed`；`--prune` 只删除 `done/` 中超过 `audio.import_keep_days` 的副本。扫描文件夹时会等 WAV 长到 RIFF 头声明的完整大小，正在同步的录音不会被转写一半。再次导入同一录音（文件名与大小相同）会落到已有会议上，而不会新建第二场。手机录音的停止清单 `<文件>.json` 随音频一起送达，在 Mac 上成为录音轨迹事件。
- `protocol.py` — 从摘要与纪要生成适合发给参会者的协议；去掉 wiki 语法，绝不包含原始逐字稿。默认最近一场会议，也可给日期或文件夹名的一部分。支持 `--style plain`、`--copy`、`--out` 和 `--graph`。
- `rename_meeting.py` — 协同重命名逐字稿、归档、图谱笔记和应用状态；默认只显示计划，`--yes` 才应用。
- `forget_meeting.py` — 从逐字稿、录音、归档、图谱引用和云端复盘快照中删除一场会议；默认只显示计划，`--yes` 才应用，并在修改幸存节点前先备份。`--keep-graph` 只处理逐字稿和录音；`--import-folder` 同时删除 `done/` 中的导入副本。

## 图谱维护

- `graph_doctor.py` — 把图谱当作记忆来体检，确定性 lint，不用模型：失效的 `[[链接]]`（活跃文件夹与归档分开统计）、内部带换行的链接、「人物」中的说话人分离标签、孤立节点、不同文件夹中的同名节点、近似重名、`_MOC.md` 覆盖率、新鲜度。不做任何修改；JSON 报告写入数据根目录的 `logs/graph_doctor.json`（晨报会读取）。`--all-graphs`（夜间任务如此调用）、`--examples N`、`--strict`（有警告则退出码 1）。
- `dedup_archive.py` — 整理历史重复归档；默认只显示计划，`--apply` 把多余文件夹移到 `Встречи-архив/_дубли/`，而不是删除。
- `dedup_graph.py` — 两条规则，各自需要单独许可：`--apply-copies` / `sufler.dedup_copies` 把 iCloud 冲突副本（与 `Имя.md` 逐字节相同的 `Имя 2.md`）移出图谱，放入 `backups/<图谱>-<hash>/dedup_copies/` 并写清单；`--apply` / `sufler.dedup_files` 把逐字节相同的归档副本换成硬链接，此后从任一路径编辑改的都是同一个文件。`--all-graphs` 遍历医生检查的所有图谱（夜间任务即如此调用）。不带参数时只输出报告。
- `merge_graphs.py` — 把分裂出去的图谱并回主图谱：新文件直接移入，Markdown 同名冲突时把捐赠方内容以「迁自……」小节追加（去掉捐赠方 frontmatter），会议条目迁入接收方的 `_MOC.md`，捐赠方的 `_MOC.md` 变为「已并入……」标记。不加 `--apply` 只显示计划；应用前会验证整个计划，拒绝二进制和非 Markdown 冲突，创建恢复备份，并在部分失败时回滚。
- `fix_action_items.py` — 一次性规范化守护进程开始规范化之前写下的纪要中的行动项格式；只改格式，已设定的状态（完成 `[x]`、取消 `[-]`、手动重开、方括号里的任何其他标记）保持不变；若改写会改变状态，该文件被跳过并列出，退出码 1。默认为空跑；`--apply` 需要 `CHAROITE_ROOT`，在图谱共享锁下写入，并把原件连同清单存入图谱备份。
- `migrate_placeholders.py` — 清理积累的说话人分离标签节点（`Люди/Собеседник N`）：它们把不同会议中的不同人粘成一个人。每个指向这类节点的链接变为纯文本，节点本身连同清单移入备份，`Люди/_ЛЮДИ.md` 重建。默认只显示计划；`--apply --backup 目录` 要求备份目录在图谱之外、数据根目录明确（`--root` 或 `CHAROITE_ROOT`），持有图谱共享锁，会议录制中则拒绝（退出码 3）。

## 夜间流程与云端处理

- `nightly.sh` — 夜间流程（应用在「设置」中安装的 launchd 任务，04:15）：等机器空闲、图谱体检、文件去重、提示记忆索引、提前生成晨报、tier3 核心修订、主题档案、可选的云端处理，然后在整理后的图谱上再生成一次晨报并跑记忆基准。各步骤相互独立：失败的一步会把当晚标记为失败，但不会取消晨报。`NIGHTLY_MAX_H`（默认 4）是整晚的时间上限。
- `wait_for_idle.py` — 等到没有会议在录制或处理、也没有变异检查在运行：夜间流程和会议处理都要驱动本地模型，同一台机器上放不下两者。等待有上限（`--timeout`，默认一小时；`0` 表示不等），退出码始终为 0——是否等到由日志说明。
- `tier3_cores.py` — 核心修订（`Ядра` 中的重复与包含）。不带参数只出报告；`--mark` 写入可撤销的「可能重复」标记；`--apply` 还会合并有把握的重复项（保留备份）。`--auto` 是夜间模式：仅在 `sufler.tier3_auto_apply: true` 时合并，否则只做标记；`--since-last` 只评判上次运行以来变化过的核心。
- `nightly_dossier.py` — 增量重建主题档案（`--full` 全部重建，`--dry` 显示计划，`--limit` 限制每次的主题数），或用 `--find` 查看检索结果。
- `nightly_dossier_review.py` — 对本地模型写好的档案做云端复核：Opus 能看出转述看不出的东西（某个决定被后来的推翻、期限已过、两个节点互相矛盾）。仅在 `sufler.cloud_edit_graph` 开启时修改，否则写建议；`--dry` 只显示不写入。
- `nightly_claude_cores.py` — 对图谱核心做云端复盘：不做修改，把建议报告写入图谱。云层（`sufler.cloud_enrich`）未开启或设置了总开关时保持沉默。
- `morning_brief.py` — 不用模型，由图谱中现成的条目、夜间修订标记和图谱体检报告组装晨报（`_Сегодня.md`）。
- `graph_search_index.py` — 会中记忆的向量索引（`src/charoite_graph/graph_search.py`）：图谱中变化的块 → 经 Ollama 的 bge-m3，缓存于 `data/graph_search/`。在实时录制之外运行——夜间以及会后短暂运行；会议中守护进程只计算查询向量。`--budget-s` 限制时间，`--stats` 查看索引状态，`--force` 录制时也建索引。
- `cloud_review.py` — 带超时和明确边界地运行会议的云端复盘，而不是把 `claude` 丢到后台就算完事。崩溃或截断的回答不再留下一个看起来像真的复盘文件。

## 模型与测量

- `get_models.py` — 一条命令装模型：`--diar`（说话人分离嵌入，没有它就无法按声音实时标注；`--model` 可选 `eres2net-base`、`eres2net-en` 或 `eres2netv2`）、`--segmentation`、`--stt sensevoice`（中文识别，228 MB）。另有 `--list`、`--check`（不联网）、`--url`、`--dest`。
- `memory_bench.py` — 用 `config/memory_bench.yaml` 中的参考问题，或在演示图谱上（`--demo`、`--demo-en`、`--demo-zh`）对整个检索闭环做基准测试。`--stats` 跳过合成，逐条打印覆盖率与闸门判定。
- `diar_bench.py` — 说话人分离的 DER：被错误标注的语音时间占比。`--make` 在本地生成合成测试样本——本仓库不可能存放会议录音；`--crosstalk` 加入重叠语音，`--wav`/`--truth` 测量你自己的录音，`--engine compare` 让当前引擎与实验性的 Nemotron 3 Diarization 对比（[详情，英文](../../DIARIZATION.md#crosstalk-and-the-nemotron-experiment)）。
- `nemotron_shadow_replay.py` 与 `nemotron_shadow_check.py` —— 无需通话即可得到 Nemotron 实时流（#478 B）的数据：回放脚本把会议录音送入真实的音频中枢、声纹跟踪器和 Nemotron 影子，输出到新的缓存目录（日志、跟踪器决策、墙钟见证）；核对脚本将影子日志与最终分离结果比对——标签延迟、“槽位 × 说话人”矩阵、与最终标注的一致度。只有数字：没有音频、没有文本，不写入数据根目录；输出只进入回放缓存目录，会议录音不存在后（保留期、“遗忘”），下次回放会删除该会议的目录；核对脚本把最终标签改名为 `f0..fN`。
- `stt_bench.py` — 识别的 CER：识别错误的字符占比。`--compare` 用同一批合成语句把 SenseVoice 与 Whisper 跑一遍对比。与说话人分离同样的提醒：合成语音比真实语音干净，这是下限而非基准。
- `bench_models.py` — 用我们自己的负载而非合成 tok/s 比较 Ollama 模型：短提示、中等抽取、真实长逐字稿，测首个 token 时间与总时间，冷启动的首次运行单独显示。
- `bench_extract.py` — 比较模型的会议分析质量而非速度：同一个 `graph_updater.extract`、同一批逐字稿（最近 3 场、`--meetings N` 或 `--files`），检查那些悄悄出错的地方——逐字稿中不存在的引文、不存在的 HH:MM 时间、抽取了多少、JSON 能否解析。原始回答写入 `logs/bench_extract/`，供人阅读。
- `gate_bench.py` — 测量决策闸门（`src/decision_gate.py`）：在每个置信阈值下，它能省掉多少次空的模型调用、会丢掉多少真实问题。`harvest` 从逐字稿收集候选供手工标注，`eval` 在已标注的 JSONL 上评分，`shadow` 读取守护进程日志中的闸门影子记录（`sufler.decision_gate_shadow: true`）。

驱动模型的基准需要空闲的机器：实时会议期间测到的是排队，而不是模型。

## 构建与发布

- `build_embedded_python.sh` — 依据带哈希的 `requirements-runtime.lock` 组装随 `Charoite.app` 一同发布的可移植 python 运行环境；自行构建应用包时先运行它，再运行 `app/make_app.sh`。
- `lock_runtime_deps.py` — 按 `pyproject.toml` 中的版本范围重建带版本与哈希的 `requirements-runtime.lock`，让签名包里装的正是审核过的依赖，而不是构建那一刻 PyPI 上的东西。需要 `uv`；结果提交到仓库。
- `build_app_icon.sh` — 由 Icon Composer 文档 `app/Resources/AppIcon.icon` 生成 macOS 应用图标：`actool`（Xcode 26+）输出供 macOS 26 使用的 `Assets.car`（没有它，Tahoe 会把旧 `.icns` 画在灰色底板里）和供 macOS ≤ 15 使用的旧版 `AppIcon.icns`；两者均已提交到仓库，macos-15 上的 CI 不会重新生成。
- `make_dmg.sh` — 由构建好的 `app/build/Charoite.app` 生成 `Charoite.dmg` 安装器（窗口里是应用和「应用程序」链接），有 Developer ID 时用同一身份签名，并为 DMG 和 zip 生成 `.sha256` 文件，供应用内更新校验。
- `notarize.sh` — 用 App Store Connect API 密钥把 zip 或 DMG 提交 Apple 公证，等待结果，被拒时打印 Apple 日志，并装订票据。由发布工作流调用。
- `sign_release_manifest.py` — 用所有者的 ed25519 密钥（它从不进入 CI）签名发布的 `.sha256` 清单并附到发布上；应用在替换自身之前要求这个签名。`--file` 不经 `gh` 签名本地文件。该步骤见[发布流程](../RELEASING.md)。

## 开发闸门

- `check_private_markers.py` — 去标识化守卫（pre-commit 钩子）：同时检查新增的行与整个被跟踪的文件树，只打印位置、绝不打印标记本身。`--all` 只扫描文件树；`--public-only` 是使用公开模式的 CI 模式。见[参与贡献](../CONTRIBUTING.md)。
- `check_test_assertions.py` — 测试必须能够失败：找出 `test_*` 函数体中没有任何可失败之处的（没有 `assert`、`pytest.raises`/`fail`/`warns`、`self.assert*`、`raise …Error`），以及位于 `return` 之后的断言。在 CI 与 pre-commit 钩子中运行；默认检查 `tests/`。
- `mutate_check.py` — 在独立的 git worktree 中把缺陷放回 `--range`（默认 `origin/main...HEAD`）中改动过的行，并要求测试变红。数据根目录由 `CHAROITE_ROOT` 指明（没有它：代码 5 并打印做法）；会议锁和 `logs/mutation.lock` 都在那里。机器正忙于会议、处理或夜间流程时拒绝启动（`--force` 可越过这一点，但越不过其他变异器的锁）；`--budget-s` 限制总时长，`--report` 在每个变异体之后重写。CI 在每个 PR 的改动行上运行它。
- `layout_map.py` — 代码布局：分层、导入边、入口点，以及代码和文档中提到的所有可执行文件路径，与 `docs/design/layout.json` 对照。不带参数时写出地图 `docs/design/layout.md`；`--check` 是闸门（CI），`--regen` 按代码刷新白名单与运行契约，`--report` 测量接缝。
- `preflight.sh` — 评审前的本地汇总：机器是否繁忙、ruff、布局闸门、隐私标记、完整 pytest、改动 `app/` 时的 Swift、区间上的变异检查。输出 `preflight: ok` 或 `FAIL: <步骤>`；重跑时 `PREFLIGHT_SKIP=mutation,swift` 可跳过步骤。

闸门的细节——运行契约、退出码、变异预算——见[参与贡献](../CONTRIBUTING.md)。
