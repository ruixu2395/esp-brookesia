# dsh-esp32-sync

运行在 DSH Host 内的 Cordis 配套插件：读取同一 Host 上 `dsh-pet 0.3.8` 的 `/state`，
把工作状态直接推送到 ESP-Brookesia / VoCat 的 `POST /api/pet/state`。
无需独立 Python 进程、模型调用或修改上游 `dsh-pet`。

安装与固件完整步骤见本工程
[DSH 直接同步说明](https://github.com/ruixu2395/esp-brookesia/blob/codex/dafeiyu-expressions/examples/agent/chatbot/DSH_PET_SYNC_CN.md)。

插件入口为 `index.cjs`（CommonJS 与 ESM 命名导出兼容），只依赖 Node.js 内置模块，
通过 `cordis.patch.yml` 挂入 DSH 配置树，注入 `webServer` 和 `connection` 服务。

默认读取 `~/.dsh/esp32-sync.json`：

```json
{
  "deviceUrl": "http://192.168.1.80:8766",
  "token": "填入两端相同的至少24字符URL安全令牌"
}
```

请用随包 `configure.cjs --device-url http://设备IP:8766` 生成有效配置和令牌。
也支持环境变量 `DSH_ESP32_CONFIG` 或插件配置项 `configFile` 指定文件。
配置变更后重启 DSH；令牌和本机 Cookie 不写入日志。

`GET /dsh-esp32-sync/status` 提供只读诊断，跟随 Host 的认证。
设备目标限定私有局域网 IPv4；同一时间只运行一个发布者对应同一设备。

开发测试：`node --test test/*.test.cjs`。
本插件尚未在用户实际 DSH 桌面版本加载验证，不声明新的 DSH 版本兼容性。
