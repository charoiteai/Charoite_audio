# 为 Charoite 做贡献

*[English](../../CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · **中文***

感谢你的关注！Charoite 是一款完全本地运行的会议助手 — 非常欢迎坚持本地优先的贡献。

项目的日常维护方式——AI 维护者流水线、评审闸门以及各自的职责——见
[MAINTENANCE](MAINTENANCE.md)。

## 基本规则

- **本地优先不容妥协。** 不调用云端、无遥测、无账号。唯一的网络目标是 localhost（Ollama 或 `mlx_lm.server`、可选的 STT 流服务与记忆伴侣）— 需手动开启的云端层（Claude CLI 与云端对话引擎）是例外，默认关闭，且每个可能携带会议数据的出口都要询问 `src/privacy.py`。两个不属于云端层的请求——版本检查与模型下载——写在 [PRIVACY](PRIVACY.md) 里；新增的请求也必须在那里写上一行。
- **俄语为先，三种界面语言。** 应用支持俄语、英语和中文：语言由 `sufler.language` 决定，每条 UI 字符串在调用处以三元组书写——`L.t(ru, en, zh)` 必须给齐三种。会议文档（提示、纪要、图谱字段值）跟随同一个键——俄语、英语或中文；STT 在俄语上最强（GigaAM），英语走 Parakeet 或 Whisper，中文走 SenseVoice 或 Whisper。标识符用英语；本仓库的注释和提交信息大多是俄语，英语同样欢迎。
- **测试必须能够失败。** 不是「覆盖了这些行」，而是行为坏掉时它会失败。要亲手验证：把缺陷放回去，确认测试变红。覆盖率抓不到这一点——一个没有任何断言的测试能 100% 覆盖代码并永远是绿的，而 CI 里的绿勾看起来像是这里受到了保护。粗糙的情形由 `scripts/check_test_assertions.py`（CI 与 pre-commit）捕获：没有 `assert`/`pytest.raises`/`raise ...Error` 的测试，以及放在 `return` 之后、执行永远到不了的断言。微妙的情形——同义反复、替换掉了被测逻辑本身的 mock——任何静态检查都找不到，只有放回去的缺陷能找到。
- **对测试有疑问，就去弄坏代码。** `CHAROITE_ROOT=$PWD scripts/mutate_check.py --range main...HEAD` 把缺陷放回改动过的行，并要求测试变红。变异器和其他入口点一样要指明数据根目录：它所让行的会议锁和它自己的 `logs/mutation.lock` 都在那里。在普通克隆中数据根目录就是克隆本身；如果数据在别处（应用的数据文件夹），就指明那个文件夹，否则锁会与夜间任务错开；没有 `CHAROITE_ROOT` 时它以代码 5 拒绝并打印做法。存活的变异体是一次无人察觉的行为变化：要么这个位置的测试存在却什么也守不住，要么根本没有测试。只变异 diff 中的行——对整个文件做一遍意味着成千上万个变异体、数小时而非数分钟。变异放在仓库的独立本地克隆中，因此测试的子进程看到的与 import 看到的是同一份被破坏的代码。每个变异体由能到达其模块的测试评判：`import X`、以子进程运行 `X.py`，或按路径加载（`spec_from_file_location("X", …)`、辅助函数 `_load("X")`）。没有任何测试以这些方式到达的模块由整套测试评判——在 CI 中很慢，而在本地基线运行可能超出时限，变异器会拒绝运行。等价变异（代码与测试从同一个常量读取的阈值）不必修——但值得一看：20.08 这样一个存活者揭示了恰好在阈值上的行为没有任何人测试。文本无法解析回同一棵被破坏语法树的变异体会带着原因报告为「未应用」（НЕ ПРИМЕНИЛОСЬ），而不是「已杀死」。有改动行却无可变异之处时结果为 `unmutable`（代码 8），并打印计划计数——文件、行、模块常量、AST 节点、无法读取的文件；空范围仍是 `nothing`（代码 6）。`--budget-s N` 是整次运行从开始算起的上限：每套基线测试之前需要剩余 4 × `--timeout`，每个变异体之前需要其测试套件的实测耗时。不够时以工具自己的一行 `прервано: бюджет`（「因预算中断」；`--force` 不能解除）停止，结果为 `partial`；若非空计划中一个变异体都没被评判，则为 `unjudged`（代码 9）——在 CI 中它是红的。`--report` 在每个变异体之后重写，因此在上限处被掐断的 job 仍会留下已检查的内容。只变异产品代码：`src/` 以及产品会启动的脚本（`layout_map.mutation_area`，按范围头部的修订版计算）；基准、工具脚本和 `tests/` 不变异。计划按 `--max` 抽样（默认 60，`all` 表示整个计划）：关键区域中的变异体全部入选，其余变异体把样本补到 `--max`，但不少于 15 个；同一层内按文件轮流选取，同一文件内按变异体身份的哈希排序（路径、函数、描述、节点的规范文本——不含行号）。修改测试或移动行号不会改变样本；CI 与本地运行评判的是同一组变异体。样本就是计划：样本之外的部分是策略，而不是「没检查完」。关键区域是 `docs/design/layout.json` 中的 `mutation_critical`（模块、脚本路径或 `模块::函数`）：声音写入磁盘或丢失、数据离开本机、所有者数据被写入或删除。该列表取范围基准与头部的并集。区域内凡导入网络库或 `safe_write` 写入门的模块，都必须列入 `mutation_critical`，或带理由列入 `mutation_not_critical`——由 `layout_map --check` 把关。关键区域中的存活者为红；区域之外的存活者为绿并列出。在 CI 中，样本分给四个分片：`--shard K/N` 取样本中每第 N 个变异体（`i % N == K-1`），每个分片在报告旁写下事实文件 `<report>.json`（格式版本、`K`、`N`、`M`——本分片的变异体数、`P`——样本、`full`——整个计划、已评判与未应用的数量、带区域的存活者、运行键）。独立的判定 job 用 `--merge-shards DIR` 合并它们；判定表在 `merge_shards` 的文档字符串里，结果出自与单次运行相同的裁判 `verdict_code`。简言之：文件未覆盖样本时为红（每个分片一个、键 1..N、ΣM = P、同一样本、区域与修订版一致、格式可读），关键区域中有变异体存活或检查本身出错时为红，样本未全部评判时为红（未评判的关键变异体会单独列出，须在合并前用 `--only-critical` 补评）；`P = 0` 时是「无可变异」的说明或盲区警告，都为绿。每次运行都在数据根下写入已评判变异体的日志 `logs/mutation_run-<键>-<k>.jsonl` 并打印运行键；`--resume <键>` 只评判日志中没有的变异体（键不匹配或日志已过期时带原因拒绝）。在本地，`--jobs N`（1–4）在一台机器上以同样方式拆分样本：父进程在只解析一次为哈希的范围上启动 N 个带 `--shard k/N` 和相同 `--max` 的普通变异器，并用 `merge_shards` 评判，退出码 0/1。允许并行运行：变异锁是共享的，四个分片在同一个 `.git` 上找到相同存活者的速度快 3.3 倍（193 秒 → 59 秒）。发给父进程的 SIGINT 或 SIGTERM 会到达每个分片，分片清理自己的副本；父进程打印 `прервано сигналом S: N из M — продолжить: --resume <键>`（「被信号 S 中断：已评判 N/M——继续：……」）并以 128 + S 退出。
- **决策放在纯函数里，循环只负责应用。** 实时回路（`stt_loop`、heartbeat 循环）是 `daemon.main()` 内的闭包——单元测试够不到它，21.08 的一次变异运行给出了数字：`daemon.py` 中 53 个变异体全部存活，而已经抽到 `src/stt_runtime.py` 的策略杀死了 15 个中的 14 个。因此实时回路的每个阈值、滞回和分支选择都是 `stt_runtime` 中的具名函数，并在 `tests/test_stt_runtime.py` 中有不变量（`progress_throttled`、`lag_transition`、`diarization_plan`、`live_input_young_enough`……）；循环调用它，别的什么都不决定。边界测试取非零的 `last`：取零时 `now - last` 与 `now + last` 无法区分——这是变异器揭示的。
- **测试在密封环境中运行。** `tests/conftest.py` 中的 autouse fixture 给每个测试一个临时数据根——任何运行都不会碰到所有者的实时数据——并关闭网络：一次请求会以 `pytest.fail` 让测试失败（调用外围的 `except Exception` 吞不掉它）。「Ollama 没开」是这个守卫的默认回答，而不是被替换的方法；需要模型的测试声明 `@pytest.mark.ollama_отвечает("模型", …)`，真正需要套接字的测试申请 `сеть_разрешена`。后台线程崩溃会让整次运行失败（`pyproject.toml` 中的 `filterwarnings`），每个测试上限 120 秒。
- **不搞模式黑名单。** 分类决策通过本地模型完成，而不是硬编码的词表 — 模式会腐烂，模型才理解上下文。

## 工作流程

1. Fork 后从 `main` 拉分支：`feat/…`、`fix/…`、`docs/…`。
2. 约定式提交（`feat(app): …`、`fix(daemon): …`）。
3. 评审之前运行 `scripts/preflight.sh`（见最后一节）：ruff、布局守卫、隐私标记、完整 pytest、触及 `app/` 时的 Swift、改动行的变异检查。对应用改动而言，这意味着 `swift build` 干净通过、`swift test --filter '^CharoiteAppTests\.'` 全绿（实测探针仅手动运行）；触碰搜索或提示词时运行 `scripts/memory_bench.py`。
4. **在同一个 PR 里更新文档** — PR 若改动代码（`src/`、`scripts/`、`app/`、`app-ios/`、`app-android/`）却不触及文档——`docs/`、`README.md`（根目录或上述文件夹的）、`PRIVACY.md`、`SECURITY.md`、`ROADMAP.md` 或 `CONTRIBUTING.md`——CI 会拦截。`CHANGELOG.md` 不算——它归 release-please 管。纯技术性改动可打 `skip-docs` 标签；依赖升级（`libs.versions.toml`、Gradle wrapper、`Package.resolved`）本身即被豁免。
5. PR 标题是约定式提交——squash 合并时它就成为 `main` 上的那条提交（[RELEASING](RELEASING.md)）。PR 描述（模板会要求）：意图、不得被破坏的不变量、影响范围，以及如何验证——对界面或声音的改动，写明在真实设备上看了什么、听了什么。

### CI 检查什么

| 时机 | 内容 |
|---|---|
| 每次 push 与 PR | `lint`：ruff、字节编译、「测试必须能够失败」、shellcheck、semgrep、mypy（参考）、示例配置的键 · `pytest (src/)`：完整 Python 测试（四个进程，`-n 4 --dist loadgroup`），随后是布局门禁 |
| 推送到 `main` 与每个 PR | CodeQL（`analyze`，另有每周定时）· 供应链：对工作流的 zizmor 与按公开格式的去标识化检查 |
| 仅 PR | 改动行的变异，分四个分片（`mutation (changed lines)`），以及它们的判定（`mutation verdict`）· 文档守卫 · 约定式 PR 标题 · dependency review（high 及以上即失败） |
| 改动 `app/`、`app-ios/` 或 `app-android/` 时 | Swift 应用构建与确定性测试（含 SwiftLint）、iOS 构建 · Android 单元测试、lint 与 debug 构建 |
| 每晚 | 同样的 Python 与 Swift 测试（macOS），外加**在模拟器中运行 iOS 测试** |

只有 `lint` 与 `pytest (src/)` 阻止合并；其余为何只是参考见 [RELEASING](RELEASING.md) 的「分支保护」。参考性检查红了，合并前仍要读。

代码检查规则只在一处：根目录 `pyproject.toml` 的 `[tool.ruff.lint]`；CI、pre-commit 与 `scripts/preflight.sh` 调用 `ruff check <路径>` 时不带任何标志，由一个测试守住这一点。除错误（`E9`、`F`）外，吞掉错误（处理器中没有 `raise`）的宽泛 `except Exception` 或 `except BaseException` 也会被拦下：收窄异常类，或标注 `# noqa: BLE001 — <理由>`；裸 `except:` 不允许。preflight 使用 CI 中固定的 ruff 版本（venv 中版本一致时用它，否则经 pipx 或 uvx）；若该引擎无法启动，该步骤记为跳过而非失败。

iOS 测试放在夜间运行是有意为之：模拟器启动很慢，把它放进 PR 的快速检查里
只会让所有人习惯等待。夜里有的是时间。

测试脚本点击的是俄文标签，而 runner 运行在英文区域设置下，因此 UI 测试用
`-ui.language ru` 启动应用——正是人们在设置里选择语言时用的那个键；而不是
去改模拟器的区域设置：这样测试检验的是应用本身，而不是 runner 镜像，在任何
语言的机器上都同样诚实。单元测试的断言走 `L.t`，不写俄文字面量：测试关心的
是行为，不是界面语言。

### 布局守卫

`docs/design/layout.json` 是 `src/` 布局的唯一真相来源：模块属于哪一层、哪些导入边逆着箭头、
哪些文件是入口点、哪些模块可以自己推导数据根。`docs/design/layout.md` 是由同样事实生成的地图，
从不手工编辑。

栈的底部分成两层。**base** 是对运行所在机器一无所知的纯辅助模块（`frontmatter`、`redirects`、
`safe_write`、`model_seam`、`exit_codes`、`media_meta`、`vocabulary`、`file_locks`、`task_line`）；
**runtime** 是应用的环境：数据根与代码根、配置、实时门、隐私、解释器配方（`charoite_paths`、
`config_loader`、`live_gate`、`privacy`、`deps`）。守卫不以字面量命名环境层：它就是根规范
（`src/charoite_paths.py`）所在的那一层。**graph** 层只允许依赖 base——图谱搜索可以脱离应用单独安装；
`graphs` 这扇从环境组装缓存目录和夜间窗口的门位于 **meeting**。llm、cloud 和 audio 可见 base 与
runtime；meeting 和 app 可见其下所有层。

**环境门禁。** 若某层的 `allowed` 不包含环境层（目前是 base 和 graph），该层的模块既不能有指向
runtime 的边——`allowed_edges` 不能豁免这种边——也不能出现 `ROOT_SHAPES` 中作用域为 `layer` 的任何
环境形式：任何环境访问（`os.environ`、`getenv`、`expandvars`、不带 `dir=` 的 `tempfile`——它读取
`TMPDIR`）、`Path.home()` / `expanduser`、`__file__`（以及 `__spec__`、`inspect.getfile`）、导入时对 `sys.path` 的任何触碰（包括
`site.addsitedir`）、动态导入。名称通过模块的导入解析（`import sys as s`、
`from importlib import import_module as im`），任何提及都算，不仅是调用
（`loader = importlib.import_module`）；`tempfile` 按调用判定。按名称片段匹配是有意保守的：
`self.home` 这样的同名者就是改名的理由。`root_exemptions` 不能豁免这些形式——工件在加载时即被拒绝；
经由邻居到达 runtime（graph → `allowed_edges` 债务 → runtime）与直接的边同样是红的。这套语法的表格
由 `tests/test_import_boundaries.py` 中的认可副本固定。这是一套语法，而非「所有方式」，与根规则的
`_env_reads` 相同：赋值绑定（`S = sys`）、`getattr`、`exec` 以及除 `tempfile` 外的隐式读取者
（`getpass.getuser`、`shutil.which`）不被识别。语法看不到的，由下面的包探针按行为发现。修复方法由同一个
`allowed` 推出：路径以参数传入，由能看见 runtime 的层中的调用方组装——就像 `graphs.open_search`
那样——而不是「去找根规范」。图谱包是其声明入口的导入闭包之并集，即 `layout.json` 中的
`package_entries`（目前有三个：`graph_search`、`embed_door` 与命令行 `cli`），而不是「全部 base 加 graph」；`tests/test_entry_points_contract.py`
把该闭包复制到临时目录，在独立进程中对演示图谱执行搜索：`HOME`、`CHAROITE_ROOT`、
`SUFLER_GRAPH_DIR`、`CHAROITE_GRAPH_DIR` 和 `TMPDIR` 都指向陷阱目录，审计钩子在读取陷阱或在
`data_dir` 之外写入时让探针失败。确定性的伪向量器让包写入向量缓存并读回，因此写入路径也被执行，
而不只是词法搜索。运行后探针把 `sys.modules` 和 `sys.path` 与导入前的状态比较：依赖泄漏和导入路径的修改
无论怎么写都会被发现。探针的自检按其自身表格的每个元素各构造一个「有漏洞」的包——每个被污染的变量、
每个文件系统变更事件、每个被隔离移除的变量——新元素若没有对应用例，测试就会变红；表格本身由按任务给出的认可副本固定，缩小表格同样是红的。

如果 PR 让这项检查变红，消息会给出修法。常见情况：

| 消息 | 含义 |
|---|---|
| 新的逆箭头边 | 导入跨越了层边界——解开它，或**附卡片**加入 `allowed_edges` |
| `allowed_edges` 含 X → Y，但该边已不存在 | 债务已还清，删除该条目 |
| 字段 X 未在 `_SCHEMA` 中声明 | 工件字段在代码中声明，每个都有类别——`measured`、`seed` 或 `decision`；添加声明（及其在 `tests/test_import_boundaries.py` 中的快照 `APPROVED_FIELDS`），而不是把键直接写进 JSON |
| 文件自己推导根 | 从 `src/charoite_paths.py` 获取，不要重新解析 `CHAROITE_ROOT`，也不要从 `__file__` 向上走 |
| 文件在导入时记住规范的答案 | 在调用时询问（`def _root(): return resolve_root(__file__)`），不要冻结在模块常量或类字段里——那个值会在入口点命名根之前就被取走 |
| 指向环境层的边 / X 层模块触碰环境 | X 层没有环境：路径或设置以参数传入，由能看见 runtime 的层上的门组装（如 `graphs.open_search`）；`allowed_edges` 不能豁免，`root_exemptions` 在加载时拒绝这些形式 |
| 包 X 拉入模块 Y | `package_entries` 中某个入口的闭包到达了带环境的层——切断该导入：包必须能脱离应用安装 |
| 地图过期 | 运行 `.venv/bin/python scripts/layout_map.py` |
| `✗ scripts/layout_map.py: …` 而不是 `✗ docs/design/layout.json: …` | 缺陷在守卫自身代码的表里（`ROOT_SHAPES`、范围表 `SHAPE_SCOPES`、`PROBLEM_KINDS`），不在工件里——修代码 |

```bash
.venv/bin/python scripts/layout_map.py           # 重新生成地图
.venv/bin/python scripts/layout_map.py --check   # CI 运行的内容，以退出码表示
.venv/bin/python scripts/layout_map.py --regen   # 按测量重写白名单
.venv/bin/python scripts/layout_map.py --report  # 接缝测量，用于规划
```

每条授予例外的记录——逆箭头的边、手动入口点、允许自己推导根的模块——都需要卡片或书面理由，
守卫**双向**比对工件与代码：不再符合现实的记录与未声明的违规同样是红的。列表只会自行缩短；
只有人写、审阅者读过的 diff 才能让它增长。

**文件夹名守卫。** 存储模式是一个值：`charoite_graph.graph_schema.GraphSchema` 声明文件夹名、章节头和
原始文件标记及其不变量，而 `src/charoite_schema.py` 以字面量保存唯一的 `CHAROITE = GraphSchema(…)` 值。
`scripts/layout_map.py` 中的守卫从类注解读取字段表、从该调用读取值，在图谱包（包探针复制的同一闭包）
的字符串字面量中寻找这些名字的副本，并把每个副本作为带卡片的 `folder_literals` 债务保存；
`folder_literal_exemptions` 以书面理由宽恕一个副本。没有卡片的命中，以及测量再也找不到的已声明记录，
都是红的。自 №422 的 PR B 起债务为零：包只询问模式的角色谓词，自身不保存任何名称常量；
`tests/test_graph_schema_roles.py` 对模式做轮换（`CHAROITE` 的每个名称换成一个 ASCII 记号），要求搜索、档案和
节点索引的可观察行为保持不变——迁移后残留的字面量会在守卫运行之前就在那里表现为不一致。一个命中只属于一个
登记表：既是债务又被宽恕的记录在加载时被拒绝。模式模块是入口闭包的普通成员：搜索以参数接收模式。

守卫中的 `KINDS` 表有意在测试内以副本固定——原因见那里的注释。改变策略意味着改两个文件，
这正是目的所在。

## 从哪里开始

- [ROADMAP](ROADMAP.md) — 我们接下来的计划
- 带 `good first issue` 标签的 issue
- [ARCHITECTURE](ARCHITECTURE.md) — 守护进程、说话人分离与图谱流水线如何协同

## 发布

release-please 根据约定式提交管理版本号 — 请勿在 PR 里手动提升版本。

## 去标识化守卫

本仓库是公开产品；其背后有一个私有项目，那里的任何个人信息都不得泄漏进来
——姓名、雇主、内部系统、路径。`scripts/check_private_markers.py` 作为
pre-commit 钩子运行，会阻止引入其中任何一项的提交。

标记列表本身是私有的，存放在 git 之外
（`~/.config/charoite/private_markers.txt`）——一份「什么不得发布」的清单本身
就很敏感。没有该文件时，钩子在 CI 之外按「失败即阻止」处理，在 CI 中（设置了 `CI`）则跳过。在 CI 之外，它还会拒绝 `git config user.email` 不是维护者地址的提交——公开仓库以同一个身份提交。两项检查在任何装了钩子的机器上都会运行，因此一个无从获得该列表的贡献者会在本地被这个钩子挡住；pre-commit 自带的 `SKIP=private-markers` 可以放行这样的提交，而下面的 CI 检查仍会在 pull request 上运行。

**CI 中的第二道防线按格式，而不是按名字。** 钩子只保护装了它的那台机器：通过网页界面的提交、没有 `pre-commit install` 的新克隆或别人的 fork 都会绕过它。因此 CI 运行 `check_private_markers.py --public-only`：它查找内部主机名、非公开域名的邮箱、个人路径以及带首字母缩写的姓氏——这些格式本身不泄露什么，却能抓住最常见的泄漏方式：从工作机器上复制来的一段配置、日志或路径。

注释里同事的名字**只有本地钩子**能抓到。请安装它——`pre-commit install`，每个克隆一次。

**检查的是每一个推送出去的提交，而不只是最终状态。** 公开仓库里的中间提交一旦推送就永久公开，即使下一个提交删掉了泄漏。`pre-commit install` 还会安装 pre-push 阶段（`private-markers-push`；本文件变更后请重新运行）：它按公开格式、以及在有私有列表时按该列表，检查每个推送出去的提交——新增的行、文件名、提交信息、作者。pre-commit 只交给钩子一对引用，因此一次推送多个分支时只检查第一个。`SKIP=private-markers-push` 可以放行推送；CI 仍会运行。维护者的克隆使用自己的钩子（在 `main` 上的主检出中运行 `check_private_markers.py --install-hooks`）：它检查推送中的每个分支，要求每个提交都使用维护者地址，并从主检出调用守卫，因此分支无法削弱它借以发布的关卡。`--range BASE..HEAD` 可手动执行同样的逐提交检查。媒体文件（图片、音频、PDF）只按文件名检查：其元数据中的文本（EXIF、PDF 作者、音频标签）不会被检查。git 视为二进制的其他文件（压缩包、办公文档、图标、含 NUL 字节的文本）无法检查，会被拒绝；维护者可将拒绝信息中给出的 blob 哈希写入 `~/.config/charoite/blob_allow.txt`，以放行该特定版本。

该钩子检查**两**样东西：本次提交新增的行，以及整个被跟踪的文件树。第二项之所以
重要，是因为*后来*才加入列表的标记，不会影响它此前已经存在的出现——之后每次提交
的 diff 都是干净的，而泄漏继续留在 `main` 里。正是这样发现了两行这类内容。按需
做全量扫描：

```bash
python3 scripts/check_private_markers.py --all   # 只打印位置，绝不打印标记本身
```

对于已发布的文件，报告只给出 `path:line` 而不含文本：这类输出会进入 CI 日志和
别人的终端，它不应该变成我们正在隐藏之物的又一份副本。

长度不超过四个字符的标记按词边界匹配：否则一个三字母缩写会命中普通单词的内部，
而一个总在狼来了的守卫，最终会被人们学会绕过。

## 运行契约与 preflight：靠运行检验，而不是靠阅读

每个可执行文件——哪些算，由 `scripts/layout_map.py` 判定：带真正 `__main__` 守卫的 Python 文件、shell 脚本——在 `docs/design/layout.json`（`run_contracts`）中都有一份**运行契约**。`help`：在隔离的数据根下 `--help` 以 0 退出。`refuse`：未命名数据根启动时以 `exit_codes.EXIT_ROOT_UNNAMED` 拒绝并打印配方——这个代码有意不是 2，2 是 argparse 给错误参数用的。`none`：没有安全的探测方式（stdio 服务器、没有 argparse 的守护进程、shell 脚本）；该文件不被运行，记录在自己的 `ticket` 字段里带上跟踪卡片（开头是 `№…`，编号后可以跟备注）——这是有主人的债务，而不是覆盖率数字。`docs/design/layout.md` 按卡片列出这笔债务，连同向上的边。`tests/test_entry_points_contract.py` 以进程方式运行每个入口点并核对契约——在 CI 中和变异检查下都是如此。新的可执行文件在代码能证明时从 `scripts/layout_map.py --regen` 获得契约（`parse_args` → `help`，根构造器 → `refuse`）；`none` 永远不由机器写——由人写，并附卡片。新增入口点时先运行 `--regen`：契约出现之前布局守卫一直是红的。

`scripts/preflight.sh [基准]`（范围 `基准...HEAD`，默认 `origin/main`）是评审轮次之前、以及接收贡献者（或沙箱执行者）工作之前的本地汇总：机器是否忙碌（正在进行的会议、会后复盘或夜间循环会让它以退出码 3 停下；`PREFLIGHT_FORCE=1` 只能在所有者同意时使用）、CI 中固定版本的 ruff、布局守卫、隐私标记（`--all`）、完整 pytest、触及 `app/` 时的 SwiftLint 与 `swift build`/`swift test`，以及改动行的变异检查。最后一行是机器结论：所有步骤都运行且通过时为 `ok`，`FAIL: <步骤>`（上方列出失败测试的名字），或当某一步无法运行时为 `неполный — пропущены: …`（「不完整——已跳过」）——跳过会被明说，绝不算作通过。`PREFLIGHT_SKIP=mutation,swift` 在重跑时跳过步骤。它在 git worktree 中同样可用：所有者的数据根取自主 checkout，因此忙碌守卫仍能看到正在进行的会议。

为什么要这样：连续五条评审发现都是关于进程行为的断言（「以代码 2 退出」「应用显示配方」），却没有人实际运行过。封住这一类问题的是一个真正运行进程的测试——而不是描述它的注释，也不是没人运行的 shell 步骤。
