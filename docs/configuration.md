# 配置 FarmWatch

所有入口读取根目录 `settings.ini`。修改后重启生效；首次安装从 `settings.example.ini` 生成，已有配置不会被覆盖。

## 常用设置

| 分组 | 用途 | 默认值 |
| --- | --- | --- |
| `mumu` | 模拟器管理、实例、CLI 和 ADB | 自动管理名为 `farm` 的实例 |
| `farm` | 提前唤醒和收获保护延迟 | 5 分钟、60 秒 |
| `updates` | 游戏资源更新检测 | 开启，最长等待 180 分钟 |
| `friends` | 访问置顶且可拿取的好友 | 开启 |
| `notify` | WxPusher 或 Server酱 | WxPusher，未配置凭据时跳过 |
| `screenshots` | 历史截图归档 | 关闭 |
| `logging` | 日志等级及保留时间 | INFO、14 天 |

好友功能保留 24 地块路线，自家务农计划优先。北京时间 00:00–08:00 不执行拿取；看到游戏明确提示次数耗尽才记录当日停止状态。

## 只运行自己的农场

```ini
[friends]
enabled = false
```

## 保存排错截图

```ini
[screenshots]
enabled = true
mode = key
retention_hours = 24
```

`key` 记录农场入口、收获及失败；`all` 保存所有取图。归档关闭时仍更新 `data/current.png`，图像识别照常工作。保留时间必须大于零，可填小数。

## 环境覆盖

优先级为 **`FARMWATCH_*` 环境变量 → INI → 默认值**。

| 环境变量 | 对应配置 |
| --- | --- |
| `FARMWATCH_MUMU_NAME` | `mumu.instance` |
| `FARMWATCH_MUMU_CLI` / `FARMWATCH_ADB` | CLI / ADB 路径 |
| `FARMWATCH_DEVICE` | ADB 地址，并默认启用手动管理 |
| `FARMWATCH_MANAGE_MUMU` | 显式开启或关闭自动管理 |
| `FARMWATCH_WAKE_AHEAD_MIN` | 提前唤醒分钟数 |
| `FARMWATCH_HARVEST_DELAY_SEC` | 收获保护秒数 |
| `FARMWATCH_FRIENDS_ENABLED` | 好友功能开关 |
| `FARMWATCH_NOTIFY_ENABLED` / `FARMWATCH_NOTIFY_CHANNEL` | 推送开关 / 通道 |
| `FARMWATCH_WXPUSHER_SPT` / `FARMWATCH_SC_SENDKEY` | 私人推送凭据 |
| `FARMWATCH_NOTIFY_DAILY_LIMIT` | 每日推送次数 |

其他环境变量可在 `farmwatch/config.py` 的 `ENV_KEYS` 查看。命令行 `--config` 可指定另一份完整配置。实际配置和 `data/` 不进入发行包。
