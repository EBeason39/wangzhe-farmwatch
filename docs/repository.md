# 仓库文件与本机环境

Git 仓库保存可以让另一台电脑重新构建项目的源码、资源、配置示例和说明。本机账号设置、运行记录与已经安装好的工具保留在本地。

## 应提交到仓库

| 文件或目录 | 内容 |
| --- | --- |
| `farmwatch/` | 全部项目代码，包括 `farmwatch/runtime/` |
| `resources/ui/1280x720/` | 控件图像及采集清单 |
| `resources/routes/` | 好友访问路线 |
| `scripts/` | 启动准备、发行构建、发行验证及安装脚本模板 |
| `tests/` | 测试代码、局部测试画面和像素保留范围清单 |
| `docs/`、`README.md`、`CHANGELOG.md` | 使用说明、结构与验证记录 |
| `LICENSE` | MIT 许可证 |
| `settings.example.ini` | 空白凭据配置示例 |
| `requirements.txt` | 程序运行依赖 |
| `requirements-dev.txt` | 引用运行依赖并补充 pytest |
| `pyproject.toml` | 测试发现范围 |
| `start_background.bat`、`stop_background.bat`、`start.bat` | 日常启停及可选前台入口 |
| `.github/workflows/ci.yml` | Windows 安装、测试、自检、构建验证 |
| `.gitignore`、`.gitattributes` | 本机文件排除与换行约定 |

`tests/fixtures/friends/` 中的昵称为 ALPHA、BRAVO、CHARLIE、DELTA、ECHO 等虚构名称。账号标题、头像及聊天区域已移除；`masking.json` 和对应测试约束公开图片的像素范围。

## 仅保留在本机

| 文件或目录 | 原因与处理 |
| --- | --- |
| `settings.ini` | 实际实例地址、路径和推送凭据；不要提交，换电脑时自行配置 |
| `.env`、`.env.*` | 本机环境和密钥；不要提交 |
| `data/` | 已学习的作物周期、好友状态和当前截图；需要迁移时单独备份 |
| `logs/` | 本机运行日志；可能含设备和账号画面识别信息 |
| `tmp/` | 进程状态、推送计数、完整诊断截图、临时构建环境 |
| 根目录 `runtime/` | 可选的本地 Python 运行时；发布源码不携带 |
| `venv/`、`venv_*/`、`.venv/` | 已安装的虚拟环境；其他电脑重新创建 |
| `platform-tools/` | 可选的本地 ADB 工具安装目录 |
| `dist/` | 生成的 ZIP；不提交到 Git，分享时可作为版本附件 |
| `AGENTS.md`、`.agents/`、`.vscode/`、`.idea/` | 本机工作区和编辑器配置 |
| `__pycache__/`、`.pytest_cache/`、覆盖率与构建缓存 | 可重新生成的开发产物 |
| 项目外备份、归档、目录兼容映射 | 本机维护文件，不进入发布目录 |

**不要混淆两个 `runtime`：根目录 `runtime/` 被忽略，`farmwatch/runtime/` 必须提交。** 若本机 `venv` 使用根目录的 Python，请把二者一起保留，不要为了上传仓库删除正在使用的环境。

## 两种发行包

| 包 | 用途 | 配置与测试 |
| --- | --- | --- |
| `dist/FarmWatch-Windows.zip` | 给日常使用者运行 | 带空白 `settings.ini` 和可选安装入口，不带测试与本机环境 |
| `dist/FarmWatch-Source.zip` | 源码交流、重新构建 | 带测试、开发依赖、CI 和完整打包输入，只带 `settings.example.ini` |

两者均包含 `LICENSE`。`release-info.json` 记录版本及逐文件 SHA-256；源码包再次构建时，除构建时间外，发行内容应保持一致。

## 提交和发行检查

```bat
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest -q
venv\Scripts\python.exe scripts\verify_release.py
git status --short
git diff --cached --stat
```

`verify_release.py` 会构建两个包、校验文件哈希、排除本机目录和实际推送凭据、独立解压运行离线自检，并从源码包再次构建两种发行包。

首次准备 Git 提交时，只添加上方“应提交”清单中的文件：

```bat
git add .github .gitignore .gitattributes README.md CHANGELOG.md LICENSE
git add farmwatch resources scripts tests docs
git add requirements.txt requirements-dev.txt pyproject.toml settings.example.ini
git add start_background.bat stop_background.bat start.bat
```

检查暂存内容后再提交并推送到自己的远程仓库。`.gitignore` 用来排除本机文件；若此前曾主动强制跟踪私人文件，需要额外从 Git 索引和历史中处理，不能仅依靠新增忽略规则。
