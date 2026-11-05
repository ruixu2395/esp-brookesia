# 智障音箱（chatbot）当前状态说明

> 本文档替代 `BUILD_AND_CLOSEUP_FIXES_CN.md`、`DSH_BRIDGE_CN.md`、`DAFEIYU_SMOOTH_32M_CN.md`、
> `WIFI_STARTUP_FIX_CN.md`。旧文档记录的是已经过时的方案（Python 桥接、12 MiB 分区、20 fps 素材），
> 已删除以免误导。

## 1. 硬件与固件配置

| 项 | 值 |
| --- | --- |
| 目标芯片 | ESP32-S3（N32R16：32 MB flash / 16 MB PSRAM） |
| 屏幕 | ST77916，360×360 圆屏，四线 SPI，**80 MHz**（`board_devices.yaml: pclk_hz`） |
| Flash 分区 | `factory` 7 MB / `anim_icon` **20 MB** / `littlefs_data` 500 KB |
| 显示底色 | **白色**（`display.cpp` 中 `lv_color_white()`） |
| 触摸手势 | **已禁用**（上滑不再打开设置页） |

分区表：`partitions_32m.csv`，总占用 28.6 MB / 32 MB。

## 2. 素材规范（`assets/dafeiyu/smooth/360_360`）

| 项 | 值 |
| --- | --- |
| 片段数 | 23 个 EAF，合计 **18.97 MB / 20 MB** |
| 画布 | 360×360（全部铺满，无小画布片段） |
| 底色 | 白色 `#FFFFFF` |
| 色数 | 96 |
| 帧数 | 40（末 3 帧向首帧混合，保证无缝循环） |
| 全身片段调色 | 饱和度 ×1.32 / 对比度 ×1.10 / 亮度 ×1.02 |
| 贴脸片段调色 | 饱和度 **×1.0**（原图色）/ 对比度 ×1.06 / 亮度 ×1.02 |
| 贴脸帧率 | **24 fps**（时长 1.67 s），全身 12 fps |
| 生成链 | 上游 webm/webp → ffmpeg 抽帧 → **Real-ESRGAN 2×** → Lanczos 回 360 → 高斯 σ0.6 去色带 → 调色 → 96 色量化 → EAF |

### 状态 → 动画映射

| DSH 状态 | 请求的 emoji | 实际片段 |
| --- | --- | --- |
| thinking | `thinking` | `thinking.eaf` |
| working | `speaking` | `working.eaf` |
| result | `thinking` | `thinking.eaf` |
| waiting | `confused` | `waiting.eaf` |
| success | `happy` | `success.eaf` |
| error | `sad` | `error.eaf` |
| 空闲 | `neutral`/`idle` | `idle_white.eaf` |

贴脸模式：`face_idle` / `face_thinking` / `face_error` / `face_success` / `face_dragging` / `face_idle_yawn`，
转场 `view_zoom`。

## 3. DSH 状态推送

| 项 | 值 |
| --- | --- |
| 传输 | 局域网 HTTP，设备监听 **TCP 8766** |
| 接口 | `POST /api/pet/state`，`Authorization: Bearer <token>` |
| 载荷 | `{schema_version:1, source:"dsh-pet", publisher_id, sequence, available, state, task}` |
| 固件侧 | `main/modules/dsh_pet_sync.cpp`（`CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE`） |
| PC 侧 | `integrations/dsh-esp32-sync`（DSH 插件，直接订阅 `session/event`，不依赖 dsh-pet） |
| 令牌 | 编译进固件，与插件配置 `~/.dsh/esp32-sync.json` 一致 |

设备超过 15 s 未收到推送会自动回到空闲（`dsh_pet_state.hpp` 的 `STALE_MS`）。

## 4. 构建与烧录

```powershell
cd examples\agent\chatbot
# 注意：环境变量 MSYSTEM 存在时 idf.py 会跳过编译直接返回 0，必须先清除
Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue
. 'D:\Espressif\idf-env.ps1'
idf.py build
idf.py -p COM6 flash
```

32 MB flash 上 `idf.py flash` 可能触发 esptool 的 `MemoryError`（`--skip-flashed` 差分分配），
绕过办法（整片写，不做差分）：

```powershell
cd build
python -m esptool --chip esp32s3 -p COM6 -b 460800 --before default-reset --after hard-reset write-flash "@flash_args"
```

分区表变更后**必须整片烧录**，只烧 app 会启动失败。NVS 中的 Wi-Fi 凭据不受影响。

## 5. 已知限制

1. **贴脸素材没有原始帧**：现有贴脸 EAF 是从已量化的旧素材反推的，无法再做真正的超分重制；
2. **彩蛋功能已移除**：曾做过 10 个 dsh-pet 彩蛋，因分区与画质取舍全部删除；
3. **贴脸动作是插值出来的**：原始只有 4 帧，补到 40 帧靠时间轴线性混合，动作偏软；
4. **分区几乎满**：18.97 / 20 MB，新增素材需先删减；
5. **状态字幕**：`SetEventMessage` 调用成功但屏幕不显示（emote 资源缺少对应 label 对象），
   当前状态靠动画本身区分。

## 6. 相关工具

| 脚本 | 用途 |
| --- | --- |
| `tools/import_dafeiyu.py` | 上游素材 → EAF（含 `encode_frame` / `encode_eaf`） |
| `tools/mask_dafeiyu_circle.py` | EAF 解码（`decode_eaf`）、圆形遮罩 |
| `tools/build_dafeiyu_assets.py` | 打包素材到 `anim_icon` 分区镜像 |
| `build/art/*.py` | 本会话用的批处理：ESRGAN、调色、白底、接缝、40 帧重采样 |
| `D:\Tools\realesrgan` | Real-ESRGAN ncnn（无需安装，命令行调用） |