# 共界 · iOS 原生版

独立运行的 SwiftUI App。世界、人物、消息、认知、结算队列与草稿保存在设备沙盒，手机直接连接模型服务。无需启动 Python、Mac 服务或 WebView。

## 打开与运行

打开 `Gongjie.xcodeproj`，选择 `Gongjie` Scheme。最低支持 iOS 17，适配 iPhone 与 iPad。

真机运行前，在 **Signing & Capabilities → Team** 选择自己的开发团队；如签名要求唯一标识，修改 Bundle Identifier。项目没有写入开发者账号或签名证书。

App 首次启动自带「白昼将死」。右上角进入模型设置，填写 HTTPS 服务地址、接口路径、模型名称和 API Key，测试连接并保存。支持 OpenAI-compatible 与 Anthropic Messages；DeepSeek V4 直接输出和校对思考模式由协议层映射。模型密钥保存在设备 Keychain，不包含在导出文件里。

世界页底部提供「存档 / 读档」，只列出当前世界实例的存档；读档前自动保存当前进度。世界页与对话工具栏均可编辑「我的角色」，这张卡只描述姓名、成年年龄、称谓、外貌与公开身份，不改写已发生的行动。

点击人物头像可查看完整立绘，点人物名称或现场卡直接进入对话。世界介绍展示背景，世界规则只在创作编辑器中维护。世界推进统一使用已确定的下一时刻，预告只出现在世界页；承诺到点后才开放赴约入口。

本轮没有启动 Xcode Preview、模拟器界面或真机 App。已经完成命令行构建及核心流程测试，界面手感由后续设备测试确认。

## 已迁移的功能

- 世界书架、新建世界、预制世界、世界与地点编辑。
- 成年角色卡：人格、语言、欲望指纹、标志行为、相遇牵引、初始关系。
- 首次相遇和每个新时段的独立入场，入场草稿经过记忆校对再保存。
- 原生流式对话、继续回复、停止生成、阅读字号、深色模式、动态字体和减少动态效果支持。
- 即时玩家回显，正文生成与整理变化分开显示；没有实际变化时不展示变化回执。
- 四名内置人物的本地头像与完整立绘、世界独立存读档，以及可编辑的玩家角色卡。
- 当前房间在场人员、各人的独立现场记忆，以及随世界存档保存的地点认知。
- 多人同场独立回应与沉默；一人结算失败时，其余人仍可回应，写回按顺序排队。
- 原文证据校验、独立语义核对、事件知情来源、关系与玩家状态落库。
- 字段或语义提案有限修复后仍不成立则拒绝本轮变化，允许继续对话；连接或存储故障保留待记录队列，重启后按原上下文恢复，期间可查看与草拟。
- 地点提及、受约束确认与真实到达分开；生成地点和别名保留各人的知情来源，不因听过名称就获得路线与隐藏描述。
- 世界脉搏、公开事实传播、独立角色行动、当前玩家状态和待履行承诺。
- 人物认知查看与公开时间线；误解只呈现所信版本。
- 世界书导入导出、完整原生故事备份导入导出、两次确认后重置。
- 本地草稿自动保存；进入后台取消生成并利用短时后台任务完成保存。

私聊与同场在同一个世界时间线上工作，返回世界不会自动推进时间。初始关系编辑用于创作底稿；已有故事的关系进展保留，重新开始时使用新底稿。

## 桌面版人物卡迁入手机

桌面数据无需升级或改写。在仓库根目录运行：

```bash
python3 ios/scripts/export-worldbook.py --list
python3 ios/scripts/export-worldbook.py --world <世界ID> --output ~/Desktop/世界书.json
```

将 JSON 通过 AirDrop 或「文件」传到手机，从书架左上角导入。导出采用已保存的创作底稿时间、人物起始地点和初始关系，创建全新的世界实例；不带旧消息、秘密事件或密钥。脚本只读数据库，不覆盖已存在的输出文件。

原生 App 的「导出完整故事」会保存原生故事进度与待结算任务；重新导入创建独立副本，不覆盖已有世界。Python SQLite 旧存档不作为原生备份文件导入。

## 结构

| 目录 | 责任 |
| --- | --- |
| `Gongjie/` | SwiftUI 页面、导航、编辑器、阅读与交互状态 |
| `Sources/GongjieCore/StoryEngine.swift`、`SceneFlows.swift` | 回合生命周期、入场、观察、地点规划与结算恢复 |
| `Sources/GongjieCore/WorldPulse.swift` | 独立行动、世界推进、可见状态规则 |
| `Sources/GongjieCore/ContextCompiler.swift`、`ContextRequests.swift` | 角色可见认知、历史、新时段上下文与来源引用 |
| `Sources/GongjieCore/WorldPresence.swift`、`WorldGeography.swift` | 实际在场、地点注册、别名与路线知情 |
| `Sources/GongjieCore/PatchValidator.swift` | 确定性证据、字段、知情范围校验与提交 |
| `Sources/GongjieCore/WorldStore.swift` | 版本化存档、原子提交、导入与重置 |
| `Sources/GongjieCore/ModelClient.swift` | HTTPS、SSE 字节解析、协议、取消、Keychain |
| `Sources/GongjieCore/Resources/` | 公共预制世界与迁移后的提示词规则 |
| `Tests/GongjieCoreTests/` | 核心链路与故障回归 |

没有第三方运行时依赖。存档采用完整 Codable 文档原子替换，不在 UI 中拼提示词或直接制造正史。失败写入不会发布部分内存状态；损坏存档会显示错误，不以空世界覆盖。

## 验证命令

```bash
cd ios
swift test
xcodebuild -project Gongjie.xcodeproj -scheme Gongjie \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/gongjie-ios-build CODE_SIGNING_ALLOWED=NO build
xcodebuild -project Gongjie.xcodeproj -scheme Gongjie -configuration Release \
  -destination 'generic/platform=iOS' \
  -derivedDataPath /tmp/gongjie-ios-device CODE_SIGNING_ALLOWED=NO build
```

`swift test` 默认运行离线测试，真实模型测试仅在显式提供 `GONGJIE_SMOKE_KEY` 与 `GONGJIE_SMOKE_PROFILE` 环境变量时运行。真实测试只使用临时世界，涵盖入场、对话、结算、推进与再入场，不读取个人故事进度。

已包含可直接打开的 Xcode 工程。若修改工程文件组织，可以在安装 XcodeGen 的环境执行 `xcodegen generate`，源配置为 `project.yml`；普通编译不依赖 XcodeGen。

内置世界书和提示词由 Web 源文件生成。在 `ios` 目录运行 `python3 scripts/sync-worldbook.py` 更新；`--check` 验证两类资源一致性。脚本只解析 Python 源码，不启动 Web 服务。

本轮验证范围及设备测试边界见 [QA.md](QA.md)。
