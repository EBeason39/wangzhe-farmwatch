# 开发与维护

FarmWatch 2.0 面向 Windows、Python 3.12 x64、MuMu 和固定的 1280×720 游戏画面。

```text
farmwatch/
  app.py              运行实例与组件组装
  config.py           配置解析与校验
  adapters/           Android ADB、模拟器生命周期
  automation/         务农循环、更新处理、好友访问
  domain/             作物记录、浇水规划、每轮结果
  vision/             取图、控件定位、OCR
  integrations/       推送通道
  runtime/            单实例、停止协议、时钟、日志
resources/
  ui/1280x720/        固定画布识别资源和采集清单
  routes/             好友访问路线
tests/                隔离测试与局部实机画面
scripts/              环境准备与发行打包
data/                 本机作物、好友状态及工作截图
logs/                 运行日志
```

## 识别契约

业务调用 `session.start`、`farm.enter`、`farm.action` 等控件标识。`vision/controls.py` 集中声明搜索区域和识别方式，业务流程不依赖图片文件名。

图像控件只在原生 1280×720 画布上匹配一次，不自动缩放，也不搜索其他分辨率目录。解码结果和模板在运行实例内缓存。

隐私同意、QQ 授权与收获继续使用文字识别：要求准确的按钮文字、限定位置及对应界面上下文；候选不唯一时不点击。同一帧的 OCR 结果复用。QQ 安卓登录入口使用实机采集的 QQ 图标，只搜索最右侧登录按钮，避免适龄提示遮挡文字导致漏检。账号检查还验证页面变化，避免重复授权点击。

`capture-manifest.json` 记录图像来源、裁剪范围和 SHA-256。更新图像时应重新获取实机画面，验证目标区域和至少一个反例；完整账号画面不加入发行包。

## 运行边界

`run` 和 `start` 共用项目互斥锁。停止协议验证进程创建时间和项目身份，先请求正常退出。作物计划、通知、好友次数与统计由独立运行实例管理。

每轮分别记录点击已发送、收获已确认、识别失败和执行失败。ADB 接受点击不等于游戏内操作已成功。

## 验证与打包

```bat
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest
venv\Scripts\python.exe -m farmwatch selftest
venv\Scripts\python.exe scripts\package.py
venv\Scripts\python.exe scripts\package.py --source
venv\Scripts\python.exe scripts\verify_release.py
```

打包使用白名单。Windows 包和源码包均以 `FarmWatch/` 为顶层目录，带文件哈希清单；源码包额外包含测试和打包脚本。私人配置、设备状态、日志、临时文件和 Python 环境均排除。

源码包保留 `scripts/share_windows/`，以便从源码再次构建相同的发行内容。Windows CI 在 Python 3.12 x64 上安装开发依赖、运行隔离测试、进行 OCR 自检，并验证两个包的独立解压和源码重建。

仓库与本地环境的完整边界见 [文件分类](repository.md)。
