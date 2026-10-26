# 大肥鱼语音贴脸模式

唤醒智能体后说 **“进入贴脸模式”**，角色以约 **1.2 秒**的缓入缓出过渡从全身拉近到脸部。
说 **“退出贴脸模式”** 或 **“恢复全身”**，按相反方向退回全身。

![360×360 素材过渡预览](./assets/dafeiyu/closeup/preview.gif)

预览为资源效果示意，实际板端还会叠加原有状态图标和字幕。
现有素材来自二维动画帧，效果通过预渲染取景、裁剪和放大实现。

## 使用和烧录

在 `idf.py menuconfig` 的 **Example Configuration** 保持以下选项开启：

- `Use Dafeiyu character animations`
- `Enable Dafeiyu voice-controlled close-up view`（默认开启）
- 使用小智时开启 `Enable Xiaozhi agent`

在 ESP-IDF 环境中重新构建并完整烧录：

```powershell
cd examples/agent/chatbot
idf.py build
idf.py -p COMx flash monitor
```

替换实际串口。**必须执行完整 `flash`**：本功能同时修改固件和 `anim_icon` 中的动画资源，
单独 `app-flash` 会缺少特写素材，串口将提示检查资源。

工程目前没有 VoCat v1.9 专用板级配置，仍需沿用已验证的板型、引脚及屏幕配置。
本功能不修改硬件引脚，资源根据屏幕配置选择。

支持的直接口令：

| 作用 | 口令 |
| --- | --- |
| 拉近 | 进入贴脸模式、开启贴脸模式、打开贴脸模式 |
| 退远 | 退出贴脸模式、关闭贴脸模式、恢复全身、恢复全身模式、进入全身模式 |

允许句首的“请”“帮我”及句尾常见标点。直接匹配完整口令，避免将
“不要进入贴脸模式”“什么是贴脸模式”等句子当作操作。
口令读取现有 Agent 的语音识别文本，仍依赖当前智能体的识别链路。

小智同时注册两个无参数 MCP 工具，方便模型理解其他自然说法：

- `Custom.Dafeiyu.EnterCloseUp`
- `Custom.Dafeiyu.ExitCloseUp`

直接口令收到识别文本后就开始切换，不必等待模型调用工具。随后重复的工具调用不会
重新播放过渡。Coze/OpenAI 也走同一语音文本事件，因此支持上表中的直接口令。

## 显示规则

- 视角与工作状态分别保存。贴脸时，说话、思考、成功、失败等表情会使用相应脸部素材；
  DSH 同步状态也使用当前视角。
- 过渡用待机姿态作为取景基准，结束后显示最新的会话/DSH 表情。过渡期间的表情更新
  只记录最新值；已经过期的云端情绪不再补播。
- 过渡为 12 帧、10 fps、约 1.2 秒。重复进入/退出不重播；反向请求在当前过渡结束后执行，
  避免突然跳回另一端。
- 小智语音与 DSH 的原有优先级继续生效，视角不会被它们的状态变化重置。
- 视角持续到退出或重启，重启恢复全身，不写入 NVS。
- 保留状态栏和字幕，不强制跳离设置页或摄像头画面；回到角色页可看到当前视角。
- 320×240 使用 320×200 的特写画布，360×360 使用 360×320，上方留给状态栏。
  1024×600 使用居中的 512×400 画布，以控制内存及 Flash 占用。

特写镜头按七组动作分别设置面部中心，避免抬手、低头时失去脸部。
全身保留 64 色，特写及过渡在小屏使用 32 色，在 1024×600 档使用 16 色。
为保留原有 **3000 KiB** 资源分区，开启本功能时使用另一套较短的动作循环；
原有全身资源保留，关闭本选项并完整烧录即可恢复原先的循环长度。
实际每组帧数和 SHA-256 记录在 `assets/dafeiyu/closeup/source.json`。
所有循环仍使用 12 fps，动作选帧保持原始间隔。

本次以 `esp_emote_assets` 0.1.2 字体/图标打包验证：

| 屏幕资源 | 全身/特写每动作不同帧数 | 分区镜像字节数 |
| --- | --- | --- |
| 320×240 | 8 / 4 | 2,986,732 |
| 360×360 | 4 / 3 | 3,004,008 |
| 1024×600 | 3 / 2 | 2,939,122 |

三档均小于 3,072,000 字节。更换字体/图标后由构建脚本重新检查容量。

## 二次开发入口

```text
语音识别事件 / 小智 MCP
  → main/modules/character_view.cpp
  → character_view_state.hpp：目标视角、过渡、最新表情、情绪有效期
  → Emote.SetEmoji / SetEmojiSegment / InsertAnimation
  → 本地 EAF 素材
```

`character_view.cpp` 的独立任务统一提交表情，避免 DSH、语音和镜头互相打断。
显示服务新增 `SetEmojiSegment(Emoji, StartFrame, EndFrame)`，以素材帧率单次播放指定的
闭区间并停在末帧。区间选择与素材选择持有同一个原生图形锁，退远不会闪现全身首帧。

`view_zoom.eaf` 的 0–11 帧用于进入，12–23 帧用于退出；两个方向共享压缩帧数据。
普通表情名保持不变，脸部版本加 `face_` 前缀。

调整取景、时长或颜色后重新生成素材：

```powershell
python examples/agent/chatbot/tools/import_dafeiyu_closeup.py --source SOURCE_DIR
```

`SOURCE_DIR` 是原先的 `QCYTSN/dsh-dafeiyu` 源码目录，提交版本见原资源 `source.json`。
生成器检查源帧 SHA-256，只读取图片，需要 Pillow。日常固件打包只使用已提交素材，
不需要 Pillow、联网取图或运行上游代码。改变帧数/时长时，还需同步修改
`character_view_state.hpp` 中的 `VIEW_FRAMES` / `TRANSITION_MS`。

## 验证边界

主机测试覆盖口令、重复指令、反向请求、过渡期间状态切换、情绪过期、渲染失败重试、
EAF 校验、资源来源、双向帧共享及所有表情的全身/特写映射。另使用原生 EAF 解码器
核验生成帧，并用实际字体/图标打包检查三档资源的分区容量。

本次通过 27 项主机测试、324 帧原生 RGB565 解码比对；视角适配模块与 Emote 服务使用
真实项目头文件及 ESP-IDF API 替身通过主机 C++ 语法检查。

```powershell
python -m unittest discover -s examples/agent/chatbot/tools/tests -v
```

当前环境没有 ESP-IDF，未完成完整固件编译、VoCat v1.9 烧录及语音/屏幕实机联调。
