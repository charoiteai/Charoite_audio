# Charoite iPhone 版 — 配套应用

*[English](../../../app-ios/README.md) · [Русский](../../ru/app-ios/README.md) · [**中文**]*

手机是放在桌上的麦克风，大脑仍在 Mac。SwiftUI 配套应用（iOS 17+，
iPhone 12 起可用）：录制会议、语音笔记和日记，并读取知识图谱——
所有重活（STT、说话人分离、LLM、图谱构建）都在您的 Mac 上完成。

## 功能

- **录音** — 三种类型：会议 / 笔记 / 日记。支持后台录音（从屏幕启动后
  可锁屏或切换应用），实时电平指示，Dynamic Island 和锁屏上的
  Live Activity 计时器，并且**「停止」按钮就在其中**：手机可以一直扣在桌上，录音也会随会议一起结束。
- **打开即录** — 打开应用，录音已经在进行（齿轮 →「打开应用即开始录音」，默认开启；类型为上次选择的；在选定投递文件夹后生效）。通话中打开？iOS 把麦克风留给通话，应用会进入待命：显示「等待麦克风」，并在通话结束的那一刻自行开始——无需再按一次，但需保持应用打开：iOS 不允许后台应用开始录音；30 分钟内返回则在返回时开始。用同一个大按钮取消。
- **免提启动** — App Intent「用 Charoite 开始录音」，可用于 Siri、快捷指令、操作按钮和轻点背面。iOS 不允许从后台开始录音：意图会打开应用，应用随即开始。iPhone 上任何应用都做不到的事：录下通话本身——麦克风归通话所有；录音中来电只是暂停。通话结束后，应用最多等待麦克风一分钟（长时间通话后 iOS 要过几秒才交还，而不是立刻），并继续同一个文件；若输入始终没有回来，就关闭该文件，会议在新文件中继续。这一分钟内再来一个通话会取消等待，因此只要 iOS 告知通话正在进行，倒计时本身就不会切断文件。暂停期间打开应用不会启动这个倒计时：它会在最长一分钟内按同样的阶梯探测输入、不切换文件，麦克风一回来就继续同一个文件。若因输入仍被占用而无法开始新文件，开始会进入待命；在后台待命无法自行触发（iOS 会挂起应用），因此录音会在再次打开应用或系统给予运行时间时接上。通话由 Mac 录制。
- **卡住录音的看门狗** — 如果文件时长超过三秒不再增长（来电、被打断、
  麦克风被抢走），屏幕会用橙色明说。更早的版本按墙上时钟计时：屏幕上
  跑了三十分钟，落进文件的却只有四十一秒，而且无从得知。不在通话中时，
  应用会自行尝试恢复；连续三次失败后关闭文件，会议在新文件中继续。编解码器
  错误和 iOS 音频服务重置也同样处理——文件保留，会议在下一个文件中继续；
  编解码器连续出错三次则如实停止录音，而不是制造空片段。
- **录音为何停止** — 每个关闭的文件旁都有一个 `<文件>.json` 清单：谁关闭的、
  为什么（「停止」按钮、通话后一分钟内麦克风未恢复、音频服务重置、编解码器
  错误、卡住——或应用崩溃时根本没有记录停止）、何时、多少秒。它与音频成对
  送到 Mac，成为该会议录音轨迹中的事件，于是分成三段送达的会议也能说明原因。
- **传送** — 录音落入您一次性选定的 iCloud Drive 文件夹（即 Mac 应用
  监视的导入文件夹）。暂时没有连接？设备端 Outbox 队列会在每次启动和
  每次停止后重发。文件以原子方式发布（先以 `.part` 复制，再重命名），
  只有在 iCloud 报告副本已上传后（在 iOS 能够判断的情况下）才离开队列：
  「已复制到文件夹」还不等于「正在送往 Mac」。语音笔记（`note_`/`diary_`
  前缀）自动进入 Mac 的笔记流水线。
- **队列完整可见** — 「排队中的录音：N」这一行可以展开为列表：录了什么、
  何时录的、多大。超过一天的条目会被标出：正常传送只需几秒，挂得更久的就
  不再是「马上就走」。在那里一键重发。
- **亲手取走录音** — 「分享录音」按钮可以把文件交到任何地方，录音文件夹也会
  出现在「文件」App 和数据线连接中（`UIFileSharingEnabled`）。传送完成后，
  最近五个录音仍留在手机上：「iCloud 收下了」并不等于「Mac 拿到了」。
- **会议列表** — 直接从所选图谱文件夹读取（第二个书签）：可移植的会议卡片
  （`Встречи-архив/*/meeting.meta.json` — 参会者、概要、决策、任务、待解决
  问题），没有卡片的旧会议则读取 `Встречи/*.md`；最新在前，点按查看卡片或
  全文。尚未从 iCloud 下载的文件会请求下载并如实跳过。
- **任务** — 图谱中所有 `- [ ]` 复选框汇于一列；勾选直接写回 markdown
  文件本身，Mac、Obsidian 和手机看到的永远一致。

## 构建与安装

需要 Xcode 16+（XcodeGen 生成的工程格式 Xcode 15 打不开）和
[XcodeGen](https://github.com/yonaskolb/XcodeGen)：

```bash
cd app-ios
export DEVELOPMENT_TEAM=<team id>   # 由 project.yml 读取；仓库中不保存 Team ID
xcodegen generate
open CharoiteiOS.xcodeproj   # 构建到设备
```

像 CI 那样不签名构建时不需要团队：`xcodebuild … CODE_SIGNING_ALLOWED=NO build`。

录音设置中显示的版本来自 `project.yml` 的 `MARKETING_VERSION`（由 release-please 递增）；
`xcodegen generate` 后检查 plist：`plutil -p Info.plist | grep CFBundleShortVersionString`
应输出 `$(MARKETING_VERSION)`，构建后的 bundle 则解析为发布版本号。构建号
（两个 target 的 `CURRENT_PROJECT_VERSION`）不由 release-please 管理：每次上传
App Store / TestFlight 前请手动递增。

测试：unit 目标（图谱解析；录音器针对通话、卡住、编解码器错误、自动开始和停止
清单的策略；传送队列）+ UI 测试。在模拟器上运行（CI 每晚运行）：

```bash
xcrun simctl privacy booted grant microphone ai.charoite.CharoiteiOS
xcodebuild -project CharoiteiOS.xcodeproj -scheme CharoiteiOS \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

## 手机上的首次设置

1. **录音标签页** → 托盘图标（↑）→ 在 iCloud Drive 中选择传送文件夹
   （例如 `Charoite Inbox` — Mac 的导入文件夹）。
2. **会议标签页** → 书本图标 → 在「文件」应用的 Obsidian 位置中
   指定图谱文件夹。

两个文件夹刻意不同——传送文件夹是录音去的地方，图谱是手机读取的内容——所以
图标也不同：橙色图标表示该文件夹尚未选择。两个选择均只需一次；security-scoped
书签在重启后依然有效。

## 隐私

应用只与您自己的 iCloud Drive 文件夹通信。无账号、无遥测、无第三方
服务。从文件夹删除录音即彻底消失——不存在隐藏副本。

## TestFlight

通过 App Store Connect API 密钥（App Manager 角色；密钥不在仓库中）以云端签名
构建并上传：

    export DEVELOPMENT_TEAM=<team id>
    xcodegen generate
    xcodebuild -project CharoiteiOS.xcodeproj -scheme CharoiteiOS \
      -destination 'generic/platform=iOS' \
      -archivePath build/CharoiteiOS.xcarchive archive \
      -allowProvisioningUpdates \
      -authenticationKeyPath ~/.config/charoite/AuthKey_<KEY_ID>.p8 \
      -authenticationKeyID <KEY_ID> -authenticationKeyIssuerID <ISSUER_ID>
    xcodebuild -exportArchive -archivePath build/CharoiteiOS.xcarchive \
      -exportOptionsPlist ExportOptions.plist -exportPath build/export \
      -allowProvisioningUpdates \
      -authenticationKeyPath ~/.config/charoite/AuthKey_<KEY_ID>.p8 \
      -authenticationKeyID <KEY_ID> -authenticationKeyIssuerID <ISSUER_ID>

`ExportOptions.plist` 中的 `destination: upload` 会把构建直接上传到 TestFlight；
同一文件里写着所有者的 `teamID`——请换成你自己的。一次性前置步骤：bundle id 通过
ASC API 注册（POST /v1/bundleIds——需要 App Manager 角色的密钥，Developer 角色的
密钥会得到 403），而应用记录只能在 ASC 中手动创建（App Store Connect → Apps →
「+」→ New App）；没有它导出会以「Error Downloading App Information」失败。
`ITSAppUsesNonExemptEncryption: false`（在生成 Info.plist 的 `project.yml` 中设置）
让每个构建免去手动回答加密问题。每次上传前递增 `CURRENT_PROJECT_VERSION`（见上文）。
