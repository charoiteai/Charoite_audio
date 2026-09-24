# 为 Charoite 做贡献

*[English](../../CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · **中文***

感谢你的关注！Charoite 是一款完全本地运行的会议助手 — 非常欢迎坚持本地优先的贡献。

项目的日常维护方式——AI 维护者流水线、评审闸门以及各自的职责——见
[MAINTENANCE](MAINTENANCE.md)。

## 基本规则

- **本地优先不容妥协。** 不调用云端、无遥测、无账号。唯一的网络目标是 localhost（Ollama、可选的大脑伴侣服务）— 需手动开启的 Claude 层是唯一例外，且默认关闭。
- **界面俄语为先，代码英语友好。** 目前 UI 字符串是俄语（英语 STT 可用；英语提示词已列入路线图）。代码、注释与提交信息使用英语。
- **不搞模式黑名单。** 分类决策通过本地模型完成，而不是硬编码的词表 — 模式会腐烂，模型才理解上下文。

## 工作流程

1. Fork 后从 `main` 拉分支：`feat/…`、`fix/…`、`docs/…`。
2. 约定式提交（`feat(app): …`、`fix(daemon): …`）。
3. 应用改动：`swift build` 干净通过、`swift test --filter '^CharoiteAppTests\.'` 全绿（实测探针仅手动运行）；守护进程：`python -m py_compile`；触碰搜索或提示词时运行 `scripts/memory_bench.py`。
4. **在同一个 PR 里更新文档** — 代码改动若不触及 `docs/`、`README*` 与 `CHANGELOG.md`，CI 会拦截（纯技术性改动可打 `skip-docs` 标签）。
5. PR 描述：改了什么、为什么改，可见之处附前后对比。

### CI 检查什么

| 时机 | 内容 |
|---|---|
| 每个 PR | 代码检查、Python 测试、应用 Swift 测试、iOS 构建、CodeQL、文档守卫 |
| 每晚 | 同样的 Python 与 Swift 测试（macOS），外加**在模拟器中运行 iOS 测试** |

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
`site.addsitedir`）、动态导入。形式按完整名称判定，而不是按模块的写法（`import sys as s`、
`from importlib import import_module as im`），并且任何提及都算，不仅是调用
（`loader = importlib.import_module`）。`root_exemptions` 不能豁免这些形式——工件在加载时即被拒绝；
经由邻居到达 runtime（graph → `allowed_edges` 债务 → runtime）与直接的边同样是红的。这是一套语法，
而非「所有方式」：除 `tempfile` 外的隐式读取者（`getpass.getuser`、`shutil.which`）不被识别。修复方法由同一个
`allowed` 推出：路径以参数传入，由能看见 runtime 的层中的调用方组装——就像 `graphs.open_search`
那样——而不是「去找根规范」。图谱包是一个声明入口的导入闭包，即 `layout.json` 中的
`package_entry`（`graph_search`），而不是「全部 base 加 graph」；`tests/test_entry_points_contract.py`
把该闭包复制到临时目录，在独立进程中对演示图谱执行搜索：`HOME`、`CHAROITE_ROOT`、
`SUFLER_GRAPH_DIR`、`CHAROITE_GRAPH_DIR` 和 `TMPDIR` 都指向陷阱目录，审计钩子在读取陷阱或在
`data_dir` 之外写入时让探针失败。确定性的伪向量器让包写入向量缓存并读回，因此写入路径也被执行，
而不只是词法搜索。探针的自检按其自身表格的每个元素各构造一个「有漏洞」的包——每个被污染的变量、
每个文件系统变更事件、每个被隔离移除的变量——新元素若没有对应用例，测试就会变红。

如果 PR 让这项检查变红，消息会给出修法。常见情况：

| 消息 | 含义 |
|---|---|
| 新的逆箭头边 | 导入跨越了层边界——解开它，或**附卡片**加入 `allowed_edges` |
| `allowed_edges` 含 X → Y，但该边已不存在 | 债务已还清，删除该条目 |
| 字段 X 未在 `_SCHEMA` 中声明 | 工件字段在代码中声明，每个都有类别——`measured`、`seed` 或 `decision`；添加声明（及其在 `tests/test_import_boundaries.py` 中的快照 `APPROVED_FIELDS`），而不是把键直接写进 JSON |
| 文件自己推导根 | 从 `src/charoite_paths.py` 获取，不要重新解析 `CHAROITE_ROOT`，也不要从 `__file__` 向上走 |
| 文件在导入时记住规范的答案 | 在调用时询问（`def _root(): return resolve_root(__file__)`），不要冻结在模块常量或类字段里——那个值会在入口点命名根之前就被取走 |
| 指向环境层的边 / X 层模块触碰环境 | X 层没有环境：路径或设置以参数传入，由能看见 runtime 的层上的门组装（如 `graphs.open_search`）；`allowed_edges` 不能豁免，`root_exemptions` 在加载时拒绝这些形式 |
| 包 X 拉入模块 Y | `package_entry` 的闭包到达了带环境的层——切断该导入：包必须能脱离应用安装 |
| 地图过期 | 运行 `.venv/bin/python scripts/layout_map.py` |

```bash
.venv/bin/python scripts/layout_map.py           # 重新生成地图
.venv/bin/python scripts/layout_map.py --check   # CI 运行的内容，以退出码表示
.venv/bin/python scripts/layout_map.py --regen   # 按测量重写白名单
.venv/bin/python scripts/layout_map.py --report  # 接缝测量，用于规划
```

每条授予例外的记录——逆箭头的边、手动入口点、允许自己推导根的模块——都需要卡片或书面理由，
守卫**双向**比对工件与代码：不再符合现实的记录与未声明的违规同样是红的。列表只会自行缩短；
只有人写、审阅者读过的 diff 才能让它增长。

守卫中的 `KINDS` 表有意在测试内以副本固定——原因见那里的注释。改变策略意味着改两个文件，
这正是目的所在。

## 从哪里开始

- [ROADMAP.zh.md](ROADMAP.md) — 我们接下来的计划
- 带 `good first issue` 标签的 issue
- `docs/ARCHITECTURE.md` — 守护进程、说话人分离与图谱流水线如何协同

## 发布

release-please 根据约定式提交管理版本号 — 请勿在 PR 里手动提升版本。

## 去标识化守卫

本仓库是公开产品；其背后有一个私有项目，那里的任何个人信息都不得泄漏进来
——姓名、雇主、内部系统、路径。`scripts/check_private_markers.py` 作为
pre-commit 钩子运行，会阻止引入其中任何一项的提交。

标记列表本身是私有的，存放在 git 之外
（`~/.config/charoite/private_markers.txt`）——一份「什么不得发布」的清单本身
就很敏感。没有该文件时，钩子在本地按「失败即阻止」处理，在 CI 中则跳过，因此
贡献者绝不会被一份他们无从获得的列表挡住。

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
