<p align="center"><img src="docs/brandmark.svg" alt="FarmWatch · 王者农场自动化" width="100%"></p>

# FarmWatch · 王者农场自动化助手

**照看作物，准时归来。** FarmWatch 是面向 Windows 和 MuMu 模拟器的王者荣耀农场助手，自动完成进入农场、一键务农、收获处理、作物识别、浇水计划和定时运行。

**完成首次环境准备后，日常只需要两个操作：**

| 你要做什么 | 双击哪个文件 |
| --- | --- |
| 启动后台自动务农 | **`start_background.bat`** |
| 停止后台自动务农 | **`stop_background.bat`** |

不需要每天打开命令行，也不需要依次运行其他安装或检查脚本。

`Windows 10 / 11` · `Python 3.12 x64` · `MuMu` · **`1280×720`** · `MIT`

## 项目特色

- **后台运行**：双击启动后，命令窗口关闭不影响后台任务；再次双击不会重复启动同一个项目。
- **自动照看自己的农场**：启动游戏、处理弹窗、进入农场、定位务农点、执行一键务农，并识别收获结果。
- **按实际作物安排浇水**：识别作物名称与剩余成熟时间，保存周期记录，计算后续浇水和收获时间。
- **等待期间释放模拟器资源**：自动管理模式下，处理完本轮后退出游戏并关闭 MuMu，临近下一次操作再启动模拟器。提前启动完成后仍会等到目标时刻再务农。
- **置顶好友访问**：可选访问置顶且可拿取的好友，按照 24 地块路线确认状态；自家农场计划优先。北京时间 00:00–08:00 不执行好友拿取。
- **QQ 登录恢复**：在账号具备 QQ 登录条件时，自动完成登录确认和授权步骤，随后继续进入游戏。首次使用仍需手动登录账号。
- **双通道推送**：支持 WxPusher 和 Server酱，可自行选择通道；未填写凭据时跳过推送，不影响务农。
- **按需保存截图**：默认只维护识别需要的当前画面。需要排错时，可开启关键节点或全部截图归档，并设置保留时间。
- **统一运行环境**：支持 MuMu 模拟器 1280×720 横屏，请在开始使用前按此要求设置分辨率。

## 第一次使用：准备环境

### 1. 准备电脑和模拟器

1. 使用 Windows 10 或 Windows 11。
2. 安装 **Python 3.12 的 64 位版本**，确保安装程序提供的 `py` 启动器或 `python` 命令可用。Python 3.13、3.14 和 32 位版本不属于当前运行范围。
3. 安装 MuMu 模拟器，创建一个名称为 **`farm`** 的实例，名称不要与其他实例重复。
4. 把该实例的分辨率设置为 **1280×720 横屏**。
5. 在模拟器中安装王者荣耀，手动完成首次账号登录，并确认能进入农场。

程序会唤起模拟器和游戏，不会唤醒已经关机的电脑。定时运行期间请让电脑保持开机且不要进入休眠。

### 2. 完整解压项目

把下载的 ZIP 完整解压到可写目录，例如 `D:\Apps\FarmWatch`。不要直接在压缩包里运行，也建议避免中文目录。

源码下载和 Windows 运行包都可以使用根目录的两个后台脚本。运行包里的 `01_install.bat`、`02_check_mumu.bat` 和 `03_start.bat` 只是安装、检查和前台调试的辅助入口，**日常使用不需要依次双击它们**。

### 3. 按需填写配置

所有配置都在根目录的 **`settings.ini`**。

- 文件已经存在：直接编辑它。
- 文件还不存在，而且默认实例名 `farm` 已符合你的环境：可以直接启动，程序会自动生成配置。
- 首次启动前就需要修改实例名或其他选项：把 `settings.example.ini` 复制一份，重命名为 `settings.ini`，再编辑。

通常保持自动管理模式即可：

```ini
[mumu]
mode = auto
instance = farm
cli =
adb =
device =
```

`cli`、`adb` 和 `device` 留空时，程序会按现有查找规则使用 MuMu 与 ADB，并读取运行实例的设备地址。实例名称不同，只需把 `instance` 改成实际名称。

### 4. 双击启动

双击 **`start_background.bat`**。

首次启动会自动检查 Python、创建项目专用的 `venv` 环境，并安装 `requirements.txt` 中的依赖，因此需要联网且可能等待一段时间。以后环境完整时会直接启动后台任务。

启动窗口提示已创建后台任务后，可以关闭这个窗口。后续运行过程写入 `logs/farmwatch.log`；创建后台进程成功并不代表已经进入游戏，实际进度以日志为准。

## 日常启动与关闭

### 开始自动务农

双击 **`start_background.bat`**。默认自动管理 MuMu，无需每次提前手动打开模拟器。

### 停止自动务农

双击 **`stop_background.bat`**。程序会请求后台正常退出；自动管理模式下也会关闭它管理的 MuMu 实例。

**关闭启动时出现的命令窗口不能代替停止脚本。** 想停止挂机，请双击 `stop_background.bat`。

### 查看前台过程

需要排错时，可以先停止后台，再双击 `start.bat` 查看控制台输出。它是可选调试入口，不是日常运行的必需步骤。

## 如何关闭或修改功能

修改配置的操作顺序：

1. 双击 **`stop_background.bat`**。
2. 用记事本或编辑器打开 **`settings.ini`**。
3. 修改对应配置项并保存。
4. 双击 **`start_background.bat`**，使新配置生效。

**修改已有分组和配置项，不要在文件末尾重复追加同名分组。** `true` 表示开启，`false` 表示关闭。只改 `settings.example.ini` 不会覆盖已存在的实际配置。

| 想调整的功能 | 配置位置 | 默认行为 |
| --- | --- | --- |
| 好友访问与拿取 | `[friends] enabled` | `true`，开启 |
| 手机消息推送 | `[notify] enabled` | `true`，无凭据时跳过 |
| 推送通道 | `[notify] channel` | `wxpusher` |
| 游戏资源更新检测 | `[updates] enabled` | `true`，开启 |
| 历史截图归档 | `[screenshots] enabled` | `false`，关闭 |
| 截图种类和保留时间 | `[screenshots] mode / retention_hours` | `key` / `72` 小时 |
| 模拟器自动开关 | `[mumu] mode` | `auto` |
| 模拟器提前启动时间 | `[farm] wake_ahead_min` | `5` 分钟 |
| 收获保护延迟 | `[farm] harvest_delay_sec` | `60` 秒 |
| 日志等级与保留时间 | `[logging] level / retention_days` | `INFO` / `14` 天 |

### 关闭好友功能，只管理自己的农场

```ini
[friends]
enabled = false
```

### 关闭所有手机推送

```ini
[notify]
enabled = false
```

需要推送时设为 `true`，并选择一个通道：

```ini
[notify]
enabled = true
channel = wxpusher
spt =
sendkey =
daily_limit = 100
```

- 使用 WxPusher：`channel = wxpusher`，在 `spt` 中填自己的 SPT 令牌。
- 使用 Server酱：`channel = serverchan`，在 `sendkey` 中填自己的 SendKey。
- 只使用选定通道，不会自动切换到另一通道。
- `daily_limit = 0` 表示不限制程序自身的每日推送次数；推送服务仍可能有自己的限制。

### 关闭或开启历史截图

不保存历史截图：

```ini
[screenshots]
enabled = false
```

只保存关键节点，保留 24 小时：

```ini
[screenshots]
enabled = true
mode = key
retention_hours = 24
```

需要保存全部取图时改成 `mode = all`。保留时间必须大于 0，可填 `0.5`、`24`、`72` 等数值。关闭归档不会影响 OCR，程序仍会更新 `data/current.png`。

### 关闭自动更新检测

```ini
[updates]
enabled = false
```

关闭后需自行处理游戏更新界面。开启时还可调整 `timeout_min`（最长等待分钟数，默认 180）和 `poll_sec`（检查间隔秒数，默认 15）。

### 修改唤醒提前量和收获延迟

```ini
[farm]
wake_ahead_min = 5
harvest_delay_sec = 60
```

提前量用于给模拟器和游戏预留启动时间，不会把真正的务农操作提前到计划时刻之前。收获延迟用于避开成熟临界点。两项均填写不小于 0 的整数。

### 自己管理模拟器开关

```ini
[mumu]
mode = manual
instance = farm
device = 127.0.0.1:16416
```

把示例地址换成你的实际 ADB 地址。手动模式要求模拟器和 ADB 已经准备好，程序不会自动开关 MuMu，但仍会控制游戏的启动、退出和农场操作。

### 调整日志

```ini
[logging]
level = INFO
retention_days = 14
```

排错时可把 `level` 改为 `DEBUG`。支持 `DEBUG`、`INFO`、`WARNING`、`ERROR`；日志保留天数必须是大于等于 1 的整数。

自动务农、收获处理、作物识别和浇水规划属于主流程，目前没有分别关闭的配置项。停止整个自动流程请使用 `stop_background.bat`。全部配置及环境变量说明见 [配置指南](docs/configuration.md)。

## 日志与常见问题

| 情况 | 处理方式 |
| --- | --- |
| 提示找不到 Python | 安装 Python 3.12 x64，确认 `py -3.12` 或 `python` 可用 |
| 找不到名为 `farm` 的实例 | 检查 MuMu 实例名称，或修改 `[mumu] instance` |
| 找不到 MuMu CLI / ADB | 在 `[mumu] cli`、`adb` 中填写真实存在的 `.exe` 绝对路径 |
| 提示分辨率不支持 | 把 MuMu 实例设为 1280×720 横屏后重启 |
| 第一次启动比较慢 | 首次会创建环境和下载依赖；查看窗口信息，等待安装完成 |
| 启动窗口关闭后不知道有没有运行 | 查看 `logs/farmwatch.log`；需要观察运行过程时，先停止后台，再使用 `start.bat` |
| 修改配置没有效果 | 确认改的是 `settings.ini`，停止后再启动；环境变量的优先级高于文件 |
| 没收到消息 | 检查推送开关、通道、对应凭据和每日次数；日志会记录推送情况 |
| 游戏要求验证码或额外手动验证 | 先停止脚本，在模拟器中完成验证，再重新启动 |
| 想保留排错画面 | 开启 `[screenshots] enabled`，问题复现后查看 `data/farm_screenshots/` 和 `tmp/login_diagnostics/` |

正常日志是 `logs/farmwatch.log`，后台初始化错误可能位于 `logs/farmwatch_bootstrap_*.log`。共享日志或截图前，请检查其中是否包含账号信息。

## 更多说明

功能配置详见 [配置指南](docs/configuration.md)。需要了解仓库文件、本机配置和备份范围，请查看 [文件分类说明](docs/repository.md)。

## 许可证

项目使用 **MIT License**，许可证全文见 [LICENSE](LICENSE)。第三方依赖、游戏图像及商标保留各自的权利与许可。

[配置指南](docs/configuration.md) · [文件分类](docs/repository.md) · [更新记录](CHANGELOG.md)
