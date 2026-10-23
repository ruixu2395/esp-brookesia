# 大肥鱼角色动画接入

本示例默认使用用户指定的 `QCYTSN/dsh-dafeiyu/assets/pet/` 角色替换原有眼睛表情。
这是预渲染的二维逐帧动画，不是 3D 模型。资源已转换为 ESP Emote 原生 EAF 格式，
固件无需 WebP 解码器。

![360×360 动画预览](./preview_360.gif)

预览来自 `esp_emote_gfx` 3.0.5 原生 EAF 解码后的 RGB565 图像，表示角色大小和动作，
不包含完整的板端状态栏、字幕和配网界面。

## VoCat 与烧录

本仓库提供 VoCat v1.0/v1.2 的配置，其中 v1.2 屏幕为 360×360；当前没有 v1.9 专用
板级定义。本次接入没有新增或修改 LCD、触摸、音频等硬件引脚。使用 VoCat v1.9 时，
请沿用已确认能正常运行此示例的板级配置；不能仅凭版本号认定 v1.2 引脚兼容 v1.9。
资源由 `CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_H_RES` / `V_RES` 自动选择，
配置为 360×360 时使用 `360_360/`。

在已加载 ESP-IDF 环境并配置好开发板的终端中运行：

```powershell
cd examples/agent/chatbot
idf.py menuconfig
idf.py build
idf.py -p COMx flash monitor
```

将 `COMx` 替换为实际串口。`Example Configuration → Use Dafeiyu character animations`
默认开启；关闭后重新编译并完整烧录，即恢复原有表情。

**首次替换及切换回原表情时都要使用 `flash`，单独 `app-flash` 不会更新 `anim_icon`
资源分区。** 开启时构建输出为 `build/anim_icon_dafeiyu.bin`，构建规则会将其加入完整烧录。
超过分区容量时构建直接失败，不会覆盖上一份有效资源镜像。

本次验证使用 `esp_emote_assets` 0.1.2 的字体、图标和布局：

| 屏幕资源档位 | 角色画布 | 每动作播放帧数 | 含字体和图标的分区镜像 |
| --- | --- | --- | --- |
| 320×240 | 216×180 | 38 | 2,986,148 字节 |
| 360×360 | 288×240 | 22 | 2,891,538 字节 |
| 1024×600 | 412×344 | 10 | 2,691,358 字节 |

均小于当前 `anim_icon` 的 3,072,000 字节（3000 KiB）。更换依赖组件的字体或图标后，
最终体积会由构建脚本重新计算。唤醒模型继续由工程的独立 `model` 分区提供。

## 状态与表情映射

| 触发状态 / 云端表情 | 角色动作 |
| --- | --- |
| 启动、停止会话、待机；neutral / relaxed | idle |
| 开始聆听；listening | dragging |
| 聆听结束且尚未说话；thinking / confused | thinking |
| 开始说话；speaking | working |
| happy / laughing / funny / loving 等 | success |
| sad / crying / angry | error |
| sleepy / sleeping | idle_yawn |
| winking / cool / surprised / shocked | dragging |

聆听时的 `dragging` 作为抬手动作使用，并未增加触摸拖拽角色的交互。
云端情绪仍通过原有 `InsertAnimation` 通道插播。表情服务由各 Agent 共用，因此切换到
Coze / OpenAI 时也会使用这一套角色动画。现有字幕、状态图标、配网二维码继续沿用。

为适配 Flash，七组动作均选取短片段，以原动作速度的 **12 fps** 往返循环，而非保留
上游完整动画。使用 64 色不透明黑底调色板，回程帧共享压缩数据。
360×360 档位每组有 12 张不同图像、22 个播放帧，约 1.83 秒循环。

## 二次开发入口

运行路径为：

```text
main/modules/display/display.cpp
  → 加载 anim_icon 资源，显示 neutral
main/modules/ai_agents.cpp
  → 订阅聆听、说话、云端情绪事件
  → EmoteHelper::SetEmoji / InsertAnimation
  → brookesia_expression_emote
  → ESP Emote EAF 播放器
```

- 改状态动作：编辑 `main/modules/ai_agents.cpp` 中的 `SetEmoji` 名称。
- 改情绪映射或帧率：编辑各分辨率目录的 `emote.json`，重新编译并完整烧录。
- 改角色位置：编辑 `tools/build_dafeiyu_assets.py` 的 `eye_anim` / `emerg_dlg` 布局，
  当前居中、Y 偏移 20，并关闭了原来双眼使用的镜像。
- 改动作选段、颜色、画布尺寸：编辑 `tools/import_dafeiyu.py`，重新导入。

日常固件构建只使用已提交的 EAF、JSON 及安装好的 ESP Emote 组件，打包脚本只依赖
Python 标准库，不需要 Pillow 或联网下载角色图片。首次安装工程组件依赖仍按 ESP-IDF
原有流程进行。

如需重新转换，在仓库根目录执行（`SOURCE_DIR` 为解压后的上游源码目录）：

```powershell
python -m pip install Pillow
python examples/agent/chatbot/tools/import_dafeiyu.py --source SOURCE_DIR --commit 43542f8fc9567f45ce98534d407abca2ff4ff74e
python -m unittest discover -s examples/agent/chatbot/tools/tests -v
```

导入工具只读取素材，不运行上游脚本。`source.json` 记录了来源提交、选帧路径、SHA-256、
尺寸和帧率；其中包含用户指定的 `dragging/dragging_078.webp`。
`preview.png` 是导入工具生成的动作联系图；`preview_360.gif` 是本次原生解码验证生成的预览，
更换资源后需重新生成 GIF。两者均不写入设备资源分区。

## 来源与授权

- 直接来源：[QCYTSN/dsh-dafeiyu](https://github.com/QCYTSN/dsh-dafeiyu/tree/43542f8fc9567f45ce98534d407abca2ff4ff74e/assets/pet)，
  固定提交 `43542f8fc9567f45ce98534d407abca2ff4ff74e`。
- 该提交的当前 `assets/pet/` 是蓝发鲸尾女仆角色，上游标注 MIT，Copyright (c) 2026
  PC2005-cloud，源自 `PC2005-cloud/dsh-pet`。
- 本目录附有原文 [LICENSE.txt](./LICENSE.txt) 和 [上游素材说明](./UPSTREAM_ASSET_LICENSE.md)。
  未使用上游 `legacy/dafeiyu/` 中的旧版角色素材。

本次完成了 6 项主机测试、全部 490 个播放帧的原生解码及 RGB565 像素比对、三档镜像与
官方打包器的逐字节比对，并检查了 CMake 资源生成与关闭开关的路径。
当前环境没有 ESP-IDF，尚未进行完整固件编译或 VoCat v1.9 实机烧录；流畅度和板级适配
需要在实际硬件上确认。
