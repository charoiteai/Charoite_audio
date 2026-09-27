# 测试

*[English](../../../tests/README.md) · [Русский](../../ru/tests/README.md) · [**中文**]*

```bash
.venv/bin/pip install pytest pytest-timeout
.venv/bin/python -m pytest tests/ -x -q
```

单个测试的超时（`pyproject.toml` 中的 `timeout = 120`）需要插件，而
`pip install .` 不会安装它（也不会安装 pytest 本身）：没有 `pytest-timeout`，
pytest 只会警告未知选项，一个卡住的 `join` 就会让整次运行挂起——恰恰在最
不希望出事的地方。

无需网络、无需模型——所有重依赖均已打桩。`conftest.py` 隔离每一个测试，
运行时不会触碰你的真实数据：

- **数据根目录与图谱。** 整个会话使用临时数据根目录（shell 中的
  `CHAROITE_ROOT` 会被暂存，运行结束后恢复），每个测试在 `tmp_path` 下有自己的
  根目录，图谱是 `tmp_path` 中的空文件夹（`CHAROITE_GRAPH_DIR`）。以根目录本身
  为测试对象的测试用 `корень_называет_тест` 标记声明，且仍须指定临时目录；
  `настоящий_корень_ревизии` 不替换云端复盘的根目录。
- **网络。** 套接字是关闭的：意外的请求会以 `pytest.fail` 让测试失败，被测代码
  中的 `except Exception` 吞不掉它。服务器场景是按完整地址设置的路由；
  `сеть_разрешена` 标记为需要真实套接字的测试解除禁令。
- **Ollama** 默认不可用；`@pytest.mark.ollama_отвечает("模型", …)` 让它以该
  模型列表作答。
- **后台线程。** 测试启动的线程崩溃会让整次运行失败
  （`PytestUnhandledThreadExceptionWarning` 视为错误），因此测试启动的每个线程
  都要在该测试内 join。

测试必须能够失败：`scripts/check_test_assertions.py`（CI 与 pre-commit）拒绝
没有任何可失败之处的测试，`scripts/mutate_check.py` 把缺陷放回改动过的行并要求
测试变红——见[参与贡献](../CONTRIBUTING.md)。

两个值得了解的守护套件：`test_privacy_defaults.py`（配置中的沉默意味着「无云端」）和 `test_cloud_call_sites.py`（每一个请求可能离开本机的位置都被注册并检查）。`test_documented_commands.py` 检查用户文档中的命令能用其中写明的解释器运行；`test_import_boundaries.py` 和 `test_entry_points_contract.py` 守护代码布局和每个入口点的运行契约。

各应用的测试在各自目录内：macOS 应用——在 `app/` 中运行
`swift test --filter '^CharoiteAppTests\.'`（`app/Probes` 中的实时探针只手动运行）；
iPhone——在模拟器上 `xcodebuild … test`（见 [app-ios](../app-ios/README.md)）；
Android——在 `app-android/` 中运行 `./gradlew testDebugUnitTest`。
