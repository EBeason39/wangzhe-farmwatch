"""FarmWatch 配置入口：INI、环境覆盖与启动前验证。"""

from __future__ import annotations

import configparser
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    """可直接展示给用户的配置错误；不包含秘密值。"""


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    manage_mumu: bool = True
    instance: str = "farm"
    cli: str = ""
    adb: str = ""
    device: str = ""
    wake_ahead_min: int = 5
    harvest_delay_sec: int = 60
    update_enabled: bool = True
    update_timeout_min: int = 180
    update_poll_sec: float = 15
    friends_enabled: bool = True
    notify_enabled: bool = True
    notify_channel: str = "wxpusher"
    notify_spt: str = field(default="", repr=False)
    notify_sendkey: str = field(default="", repr=False)
    notify_daily_limit: int = 100
    screenshots_enabled: bool = False
    screenshots_mode: str = "key"
    screenshots_retention_hours: float = 72
    log_level: str = "INFO"
    log_retention_days: int = 14


ENV_KEYS = {
    ("mumu", "instance"): "FARMWATCH_MUMU_NAME",
    ("mumu", "cli"): "FARMWATCH_MUMU_CLI",
    ("mumu", "adb"): "FARMWATCH_ADB",
    ("mumu", "device"): "FARMWATCH_DEVICE",
    ("farm", "wake_ahead_min"): "FARMWATCH_WAKE_AHEAD_MIN",
    ("farm", "harvest_delay_sec"): "FARMWATCH_HARVEST_DELAY_SEC",
    ("updates", "enabled"): "FARMWATCH_UPDATE_ENABLED",
    ("updates", "timeout_min"): "FARMWATCH_UPDATE_TIMEOUT_MIN",
    ("updates", "poll_sec"): "FARMWATCH_UPDATE_POLL_SEC",
    ("friends", "enabled"): "FARMWATCH_FRIENDS_ENABLED",
    ("notify", "enabled"): "FARMWATCH_NOTIFY_ENABLED",
    ("notify", "channel"): "FARMWATCH_NOTIFY_CHANNEL",
    ("notify", "spt"): "FARMWATCH_WXPUSHER_SPT",
    ("notify", "sendkey"): "FARMWATCH_SC_SENDKEY",
    ("notify", "daily_limit"): "FARMWATCH_NOTIFY_DAILY_LIMIT",
    ("screenshots", "enabled"): "FARMWATCH_SCREENSHOTS_ENABLED",
    ("screenshots", "mode"): "FARMWATCH_SCREENSHOTS_MODE",
    ("screenshots", "retention_hours"): "FARMWATCH_SCREENSHOTS_RETENTION_HOURS",
    ("logging", "level"): "FARMWATCH_LOG_LEVEL",
    ("logging", "retention_days"): "FARMWATCH_LOG_RETENTION_DAYS",
}


def _boolean(value, label):
    word = str(value).strip().lower()
    if word in {"true", "yes", "on", "1"}:
        return True
    if word in {"false", "no", "off", "0"}:
        return False
    raise ConfigError(f"{label} 必须为 true 或 false")


def load_settings(path=None, *, environment=None, root=None):
    """读取 UTF-8 INI；环境变量优先。此函数不启动设备、不写文件。"""
    root = Path(root or ROOT).resolve()
    path = Path(path) if path is not None else root / "settings.ini"
    env = os.environ if environment is None else environment
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        try:
            with path.open(encoding="utf-8-sig") as file:
                parser.read_file(file)
        except (OSError, UnicodeError, configparser.Error) as exc:
            raise ConfigError("settings.ini 无法读取或 INI 格式错误，请检查编码和重复项目") from exc
    elif path != root / "settings.ini":
        raise ConfigError("指定的配置文件不存在")

    allowed = {}
    for section, key in ENV_KEYS:
        allowed.setdefault(section, set()).add(key)
    allowed["mumu"].update({"mode", "port"})
    for section in parser.sections():
        if section not in allowed:
            raise ConfigError(f"未知配置分组 [{section}]")
        for key in parser[section]:
            if key not in allowed[section]:
                raise ConfigError(f"未知配置项 [{section}] {key}")

    def read(section, key, default=""):
        env_key = ENV_KEYS.get((section, key))
        value = env.get(env_key) if env_key else None
        return str(value if value is not None else parser.get(section, key, fallback=str(default))).strip()

    def flag(section, key, default):
        return _boolean(read(section, key, default), f"[{section}] {key}")

    def number(section, key, default, minimum, *, integer=False, exclusive=False):
        label = f"[{section}] {key}"
        try:
            value = int(read(section, key, default)) if integer else float(read(section, key, default))
        except ValueError as exc:
            raise ConfigError(f"{label} 必须为{'整数' if integer else '数值'}") from exc
        if not math.isfinite(value) or value < minimum or (exclusive and value == minimum):
            raise ConfigError(f"{label} 必须{'大于' if exclusive else '不小于'} {minimum}")
        return value

    mode = read("mumu", "mode", "auto").lower()
    if mode not in {"auto", "manual"}:
        raise ConfigError("[mumu] mode 只能为 auto 或 manual")
    device = read("mumu", "device")
    port = read("mumu", "port")  # 兼容旧分享包只填写端口的配置。
    if not device and port:
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ConfigError("[mumu] port 必须为 1–65535 的端口")
        device = f"127.0.0.1:{int(port)}"
    manage = mode == "auto"
    if str(env.get("FARMWATCH_DEVICE", "")).strip():
        manage = False
    if "FARMWATCH_MANAGE_MUMU" in env:
        manage = _boolean(env["FARMWATCH_MANAGE_MUMU"], "FARMWATCH_MANAGE_MUMU")
    instance = read("mumu", "instance", "farm")
    if not instance:
        raise ConfigError("[mumu] instance 不能为空")
    if not manage and not device:
        raise ConfigError("手动模式需要填写 [mumu] device 或 port")

    def executable(key):
        value = read("mumu", key).strip('"')
        if value:
            candidate = Path(os.path.expandvars(value)).expanduser()
            if not candidate.is_absolute() or not candidate.is_file() or candidate.suffix.lower() != ".exe":
                raise ConfigError(f"[mumu] {key} 应为存在的 .exe 绝对路径")
            return str(candidate.resolve())
        return ""

    channel = read("notify", "channel", "wxpusher").lower()
    channel = {"sct": "serverchan", "ftqq": "serverchan"}.get(channel, channel)
    if channel not in {"wxpusher", "serverchan"}:
        raise ConfigError("[notify] channel 只能为 wxpusher 或 serverchan")
    screenshot_mode = read("screenshots", "mode", "key").lower()
    if screenshot_mode not in {"key", "all"}:
        raise ConfigError("[screenshots] mode 只能为 key 或 all")
    level = read("logging", "level", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("[logging] level 只能为 DEBUG、INFO、WARNING 或 ERROR")
    return Settings(
        root=root, manage_mumu=manage, instance=instance, device=device,
        cli=executable("cli"), adb=executable("adb"),
        wake_ahead_min=number("farm", "wake_ahead_min", 5, 0, integer=True),
        harvest_delay_sec=number("farm", "harvest_delay_sec", 60, 0, integer=True),
        update_enabled=flag("updates", "enabled", True),
        update_timeout_min=number("updates", "timeout_min", 180, 1, integer=True),
        update_poll_sec=number("updates", "poll_sec", 15, 2),
        friends_enabled=flag("friends", "enabled", True),
        notify_enabled=flag("notify", "enabled", True), notify_channel=channel,
        notify_spt=read("notify", "spt"), notify_sendkey=read("notify", "sendkey"),
        notify_daily_limit=number("notify", "daily_limit", 100, 0, integer=True),
        screenshots_enabled=flag("screenshots", "enabled", False),
        screenshots_mode=screenshot_mode,
        screenshots_retention_hours=number("screenshots", "retention_hours", 72, 0, exclusive=True),
        log_level=level,
        log_retention_days=number("logging", "retention_days", 14, 1, integer=True),
    )


def initialize_settings(root=ROOT):
    """首次启动复制配置示例；已有配置始终保持原值。"""
    root = Path(root)
    target = root / 'settings.ini'
    if not target.exists():
        target.write_bytes((root / 'settings.example.ini').read_bytes())
    return target
