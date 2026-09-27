# 测试

*[English](../../../tests/README.md) · [Русский](../../ru/tests/README.md) · [**中文**]*

```bash
.venv/bin/python -m pytest tests/ -x -q
```

图谱包的 wheel 测试在离线、无构建隔离的情况下构建 wheel，因此 venv 中需要安装与 `ci.yml` 的 `env` 中固定版本一致的 `setuptools`；Python 3.12 的 venv 默认不带它。缺失或版本不同时，测试会失败并给出确切命令（`<同一个 python> -m pip install setuptools==<固定版本>`）。

无需网络、无需模型——所有重依赖均已打桩。两个值得了解的守护套件：`test_privacy_defaults.py`（配置中的沉默意味着「无云端」）和 `test_cloud_call_sites.py`（每一个请求可能离开本机的位置都被注册并检查）。Swift 应用测试在各应用目录内，通过 `swift test` / `xcodebuild test` 运行。
