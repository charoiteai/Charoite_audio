# Charoite iPhone 版 — 配套应用

*[English](../../../app-ios/README.md) · [Русский](../../ru/app-ios/README.md) · [**中文**]*

手机是放在桌上的麦克风，大脑仍在 Mac。SwiftUI 配套应用（iOS 17+，
iPhone 12 起可用）：录制会议、语音笔记和日记，并读取知识图谱——
所有重活（STT、说话人分离、LLM、图谱构建）都在您的 Mac 上完成。

## 功能

- **录音** — 三种类型：会议 / 笔记 / 日记。支持后台录音（从屏幕启动后
  可锁屏或切换应用），实时电平指示，Dynamic Island 和锁屏上的
  Live Activity 计时器。
- **卡住录音的看门狗** — 如果文件时长超过三秒不再增长（来电、被打断、
  麦克风被抢走），屏幕会用橙色明说。更早的版本按墙上时钟计时：屏幕上
  跑了三十分钟，落进文件的却只有四十一秒，而且无从得知。
- **传送** — 录音落入您一次性选定的 iCloud Drive 文件夹（即 Mac 应用
  监视的导入文件夹）。暂时没有连接？设备端 Outbox 队列会在每次启动和
  每次停止后重发。语音笔记（`note_`/`diary_` 前缀）自动进入 Mac 的
  笔记流水线。
- **队列完整可见** — 「排队：N」这一行可以展开为列表：录了什么、何时录的、
  多大。超过一天的条目会被标出：正常传送只需几秒，挂得更久的就不再是
  「马上就走」。在那里一键重发。
- **亲手取走录音** — 「分享录音」按钮可以把文件交到任何地方，录音文件夹也会
  出现在「文件」App 和数据线连接中（`UIFileSharingEnabled`）。传送完成后，
  最近五个录音仍留在手机上：「iCloud 收下了」并不等于「Mac 拿到了」。
- **会议列表** — 直接读取所选图谱文件夹中的 `Встречи/*.md`（第二个
  书签），最新在前，点按查看全文。尚未从 iCloud 下载的文件会请求下载
  并如实跳过。
- **任务** — 图谱中所有 `- [ ]` 复选框汇于一列；勾选直接写回 markdown
  文件本身，Mac、Obsidian 和手机看到的永远一致。

## 构建与安装

需要 Xcode 15+ 和 [XcodeGen](https://github.com/yonaskolb/XcodeGen)：

```bash
cd app-ios
xcodegen generate
open CharoiteiOS.xcodeproj   # 选择您的开发者团队，构建到设备
```

测试：unit 目标（图谱解析）+ UI 测试。在模拟器上运行：

```bash
xcrun simctl privacy booted grant microphone ai.charoite.CharoiteiOS
xcodebuild -project CharoiteiOS.xcodeproj -scheme CharoiteiOS \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

## 手机上的首次设置

1. **录音标签页** → 文件夹图标 → 在 iCloud Drive 中选择传送文件夹
   （例如 `Charoite Inbox` — Mac 的导入文件夹）。
2. **会议标签页** → 「选择文件夹」→ 在「文件」应用的 Obsidian 位置中
   指定图谱文件夹。

两个选择均只需一次；security-scoped 书签在重启后依然有效。

## 隐私

应用只与您自己的 iCloud Drive 文件夹通信。无账号、无遥测、无第三方
服务。从文件夹删除录音即彻底消失——不存在隐藏副本。
