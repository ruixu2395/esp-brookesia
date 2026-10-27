# 构建修复与圆屏贴脸（2026-10-27 会话记录）

本文件记录一次连续的调试会话：从 `idf.py bmgr` 构建立不起来，到贴脸模式能正常显示、
按 360×360 圆屏重裁素材，最后切到 N32R16 的 20 fps 配置。

## 环境

| 项 | 值 |
| --- | --- |
| IDF | ESP-IDF v6.1（`D:\.espressif\v6.1\esp-idf`） |
| 目标 | `esp32s3` |
| 板级 | `esp_vocat_board_v1_0`（ST77916，360×360 圆形屏） |
| 模组 | ESP32-S3 **N32R16**（32 MB flash / 16 MB PSRAM） |
| 示例 | `examples/agent/chatbot` |

---

## 1. 构建链路修复

### 1.1 `bmgr` 不接受裸板名

```
idf.py bmgr esp_vocat_board_v1_0      # ✗ 板名会以位置参数传入
idf.py bmgr -b esp_vocat_board_v1_0   # ✓
```

`esp_board_manager` 的 `idf.py` 扩展在检测到裸参数时会直接 `FatalError`，并提示改用 `-b`。
另外切换板子时脚本会**备份并删除** `sdkconfig`（备份到
`components/gen_bmgr_codes/sdkconfig.bmgr_board.old`），并用生成的
`board_manager.defaults` 设置 `CONFIG_IDF_TARGET="esp32s3"`。

之前 `sdkconfig`/`build/` 是 **esp32** 目标生成的，与 `IDF_TARGET=esp32s3` 冲突，需要先清掉
`build/`（`sdkconfig` 由 bmgr 处理）。

### 1.2 `littlefs_create_partition_image` 未定义

该 CMake 函数只有在 `joltwallet/littlefs` 组件进入构建时才存在，而它是
`brookesia_hal_adaptor` 的**条件依赖**（要求 storage device + file system + littlefs 三个
Kconfig 同时开启）。板级配置没应用时函数不存在，configure 直接失败：

```
CMake Error at main/CMakeLists.txt:19 (littlefs_create_partition_image):
  Unknown CMake command "littlefs_create_partition_image".
```

`examples/system/super` 早已有守卫，这里补齐同样的守卫（`main/CMakeLists.txt`）。

### 1.3 `hal_adaptor` 的 5 个补丁文件损坏

`hal/brookesia_hal_adaptor/tools/*.patch` 中，**hunk 内的空上下文行丢了行首空格**，
`git apply` 直接报 `corrupt patch`：

```
error: corrupt patch at line 12
```

这些补丁以 `REQUIRED` 应用，失败即中断 configure
（`brookesia_hal_adaptor: patch does not apply to espressif__esp_board_manager`）。
修复方式：把 hunk 内的空行补回一个空格；`git apply --check` 全部通过后，configure 里出现
`brookesia_hal_adaptor: applied patch to espressif__esp_board_manager`。

### 1.4 `main` 缺少 `esp_lvgl_adapter` 依赖

`main/modules/display/display.cpp` 直接 `#include "esp_lv_adapter.h"` 并调用
`esp_lv_adapter_lock()/unlock()`，但 `brookesia_gui_lvgl` 只把它声明为 **private** 依赖，
include 目录不会传播到 `main`：

```
fatal error: esp_lv_adapter.h: No such file or directory
```

修复：`main/idf_component.yml` 增加 `espressif/esp_lvgl_adapter: 0.6.*`（`require: private`），
`main/CMakeLists.txt` 的 `PRIV_REQUIRES` 增加 `espressif__esp_lvgl_adapter`。

### 1.5 并发构建互相踩踏

同一 `build/` 目录并行跑两个 `idf.py build` 时，ninja 会报
`failed recompaction: Permission denied`（`.ninja_deps` 被另一进程占用）。
排查时先确认没有残留的 `ninja` / `idf.py` 进程。

---

## 2. 表情服务 handler 顺序错位（本次最关键的一个 bug）

### 现象

- 启动日志：`Failed to set emote event message: Parameter not found: TimeoutMs`
  → `Failed to start expression emote`；
- 之后每秒一条 `CharacterView: Could not apply character view; check the flashed animation assets`；
- 说“进入贴脸模式”后，**旧的全身画面仍然留着**（旧动画层没有被停掉）。

### 根因

`ServiceBase::register_functions()` 用**下标**把 schema 和 handler 配对：

```cpp
for (size_t i = 0; i < total_count; ++i) {
    impl_->function_registry->add(std::move(schemas[i]), std::move(handlers[i].handler));
}
```

而 `brookesia_expression_emote` 的 `get_function_handlers()` 把 `SetEmojiSegment` 的 handler
插在了第 4 位，`FUNCTION_SPECS` 里它却在**最后一个**。于是第 4 位之后全部串位：

| 调用 | 实际执行的实现 | 报错 |
| --- | --- | --- |
| `StopAnimation` | `InsertAnimation` | `Parameter not found: Animation` |
| `WaitAnimationFrameDone` | `StopAnimation` | — |
| `SetEventMessage` | `WaitAnimationFrameDone` | `Parameter not found: TimeoutMs` |
| `HideEventMessage` / `SetQrcode` / `HideQrcode` / `NotifyFlushFinished` / `RefreshAll` | 各自前一个 | — |

`StopAnimation` 从未真正执行 → 进入贴脸时旧动画层不会被清除，`view_zoom` 只能叠在旧画面上。

### 修复

`service/expression/brookesia_expression_emote/include/brookesia/expression_emote/emote.hpp`：
把 `SetEmojiSegment` 的 handler 移到列表末尾，并加注释说明“顺序必须与
`FunctionId` / `FUNCTION_SPECS` 一致”。

修复后设备日志中 `StopAnimation:` / `Failed to set emote event message` /
`Could not apply character view` 三个错误全部消失。

---

## 3. 显示初始化健壮性

- `display.cpp`：`SetEventMessage` 只负责状态栏文字，失败**不再**中断 emote 初始化
  （原来它一失败就 `Failed to start expression emote`，导致整块不显示）；同时显式传
  `(Event, Message)` 两个位置参数。
- `character_view.cpp`：`StopAnimation` 改为“尽力而为”，并对
  `SetEmojiSegment` / `SetEmoji` / `InsertAnimation` 分别打印失败原因，便于定位。

---

## 4. 圆屏贴脸素材

### 几何

- 面板：360×360（`board_devices.yaml` 的 `x_max/y_max`），圆屏。
- 贴脸帧：360×320；`build_dafeiyu_assets.py` 里布局为 `align=CENTER, y=+20`
  → 帧放在面板 **y=40..360**，**帧中心比屏幕圆心低 20 px**，顶部 40 px 是状态栏。
- 因此“帧内切圆”会得到直径 320、且偏下 20 px 的圆（比屏幕小一圈）。

### 工具：`tools/mask_dafeiyu_circle.py`

不依赖上游源帧，直接对已生成的 EAF 做二次加工：

- 解码 EAF（索引 RLE / Huffman-RLE / 直接 Huffman 三种 block，含调色板）；
- 按模式遮罩；
- 复用 `import_dafeiyu.encode_frame/encode_eaf` 重新编码；
- `--verify` 做解码→重编码→解码的往返校验（当前 8 个片段 `max_pixel_diff=0`）。

| 模式 | 圆的定义 | 用途 |
| --- | --- | --- |
| `panel` | 面板圆：r=180，圆心在帧内 y=140 | 保留 360×320 帧时的正确圆形 |
| `mask` | 帧内切圆 | 仅想裁圆角 |
| `fill` | 1.414× 放大后内切 | 更“贴脸”，会切到眼睛，不推荐 |
| `square` | 先把帧扩到 360×360（1.125× 放大 + 居中裁切），再内切圆 | **最终采用**：顶部黑带消失 |

配合 `build_dafeiyu_assets.py`：360 closeup 的布局 `y` 由 20 改为 **0**，让 360×360 的帧
正好压在面板中心；其它分辨率保持 y=20。

### 体积

| 包 | 帧数（face_idle / view_zoom） | 打包后 `anim_icon_dafeiyu.bin` |
| --- | --- | --- |
| closeup 12 fps | 36 / 36 | ~2.9 MB（3 MiB 分区也可容纳） |
| smooth 20 fps | 86 / 48 | **7,173,755 / 12,582,912**（12 MiB 分区） |

---

## 5. 切到 N32R16（20 fps）

仓库本来就有 20 fps 的 smooth 包，但需要 12 MiB 的 `anim_icon`，16 MB flash 放不下。
`examples/agent/chatbot/sdkconfig.defaults` 增加：

```ini
CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_32m.csv"
CONFIG_PARTITION_TABLE_FILENAME="partitions_32m.csv"
CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M=y
```

分区（`partitions_32m.csv`）：`anim_icon` 偏移 `0xcaa000`、大小 `0xc00000`（12 MiB），
整表约 26 MB，留出 6 MB 余量。

固件侧无需改代码：`character_view_state.hpp` 已有
`#if CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M` 分支（`VIEW_FRAMES=24`、`FACE_LOOP_MS=4300`），
`display.cpp` 也会把 emote 渲染帧率设为 20。

> 回到 N16R8 时删掉上面四行即可恢复 16 MB + `partitions_16m.csv`。

---

## 6. 改动清单

| 文件 | 说明 |
| --- | --- |
| `hal/brookesia_hal_adaptor/tools/*.patch`（5 个） | 修复 hunk 内空行丢失行首空格导致的 `corrupt patch` |
| `examples/agent/chatbot/main/CMakeLists.txt` | littlefs 守卫；`PRIV_REQUIRES` 增加 `espressif__esp_lvgl_adapter` |
| `examples/agent/chatbot/main/idf_component.yml` | 增加 `espressif/esp_lvgl_adapter`（private） |
| `service/.../brookesia_expression_emote/.../emote.hpp` | handler 顺序与 schema 对齐（核心修复） |
| `examples/agent/chatbot/main/modules/display/display.cpp` | emote 初始化不因状态栏文字失败而中断 |
| `examples/agent/chatbot/main/modules/character_view.cpp` | `StopAnimation` 尽力而为 + 分步错误日志 |
| `examples/agent/chatbot/tools/mask_dafeiyu_circle.py` | 新增：EAF 圆形/整面板重裁工具 |
| `examples/agent/chatbot/tools/build_dafeiyu_assets.py` | 360 closeup 布局 `y=0` |
| `examples/agent/chatbot/assets/dafeiyu/{closeup,smooth}/360_360/*.eaf` | 8 个贴脸片段重裁为 360×360 圆形 |
| `examples/agent/chatbot/sdkconfig.defaults` | 32 MB flash + `partitions_32m.csv` + smooth 20 fps |

## 7. 复现命令

```powershell
# 1) 板级配置（只需一次）
idf.py bmgr -b esp_vocat_board_v1_0

# 2) 构建
idf.py build

# 3) 完整烧录（分区表变了，必须整片烧）
idf.py -p COMx flash

# 4) 素材重裁（可选，幂等）
python tools/mask_dafeiyu_circle.py --verify
python tools/mask_dafeiyu_circle.py --apply --mode square
python tools/mask_dafeiyu_circle.py --assets ../assets/dafeiyu/smooth/360_360 --apply --mode square
```

## 8. 待办

- 板子最后一次未在线，**最新固件（32 MB / 20 fps / 360×360 圆形贴脸）尚未烧录验证**。
- 若要继续提升到 20 fps 以上的“真实帧”（而不是加快播放），需要 `waifu2x-ncnn-vulkan`
  重跑 `tools/prepare_dafeiyu_quality.py --smooth`；本机有 `ffmpeg`（libvpx-vp9）与 GPU，
  但没有 waifu2x 可执行文件。
- `mask_dafeiyu_circle.py` 目前只处理 360×360 的贴脸片段；320×240 / 1024×600 的
  closeup 仍是方形。
