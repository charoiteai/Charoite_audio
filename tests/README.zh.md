# 测试

*[English](README.md) · [Русский](README.ru.md) · [**中文**]*

```bash
.venv/bin/python -m pytest tests/ -x -q
```

无需网络、无需模型——所有重依赖均已打桩。两个值得了解的守护套件：`test_privacy_defaults.py`（配置中的沉默意味着「无云端」）和 `test_cloud_call_sites.py`（每一个请求可能离开本机的位置都被注册并检查）。Swift 应用测试在各应用目录内，通过 `swift test` / `xcodebuild test` 运行。
