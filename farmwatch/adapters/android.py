"""王者农场助手：device。所有可变运行状态归 FarmApp 实例所有。"""

from __future__ import annotations
import os
import re
import subprocess
import shutil as _shutil
from pathlib import Path
import farmwatch.adapters.emulator as mumu_control


def resolve_adb(settings):
    """选用同一 ADB 客户端；优先用户配置和 MuMu 自带版本。"""
    if settings.adb:
        return settings.adb
    candidates = []
    if settings.cli:
        candidates.append(Path(settings.cli).with_name('adb.exe'))
    candidates.extend(Path(p).with_name('adb.exe') for p in mumu_control.DEFAULT_MUMU_CLI_PATHS)
    candidates.append(settings.root / 'platform-tools' / 'adb.exe')
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return _shutil.which('adb') or 'adb'


class DeviceActions:
    def wake_screen(self):
        """唤醒模拟器并关闭无密码锁屏，不执行手机亮度或密码操作。"""
        output = self.adb_shell('dumpsys power | grep mHoldingDisplaySuspendBlocker')
        if 'mHoldingDisplaySuspendBlocker=true' in output:
            return True
        self.adb_shell('input keyevent KEYCODE_WAKEUP')
        self.time.sleep(1)
        self.adb_shell('wm dismiss-keyguard')
        return True

    def _env_flag(self, value, default):
        """Parse a conventional boolean environment value with a fallback."""
        if value is None:
            return bool(default)
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _resolve_manage_mumu(self,
        device_from_env, configured_value, platform_is_windows=None
    ):
        """Resolve MuMu management with a Windows-only automatic default."""
        if platform_is_windows is None:
            platform_is_windows = os.name == "nt"
        default = bool(platform_is_windows) and not bool(device_from_env)
        return self._env_flag(configured_value, default)

    def _resolve_mumu_config(self, environment=None, platform_is_windows=None):
        """Resolve the device source and MuMu-management flag used at import."""
        environment = os.environ if environment is None else environment
        device_env_value = environment.get("FARMWATCH_DEVICE")
        if isinstance(device_env_value, str):
            device_env_value = device_env_value.strip() or None
        else:
            device_env_value = device_env_value or None
        device_from_env = device_env_value is not None
        manage_mumu = self._resolve_manage_mumu(
            device_from_env,
            environment.get("FARMWATCH_MANAGE_MUMU"),
            platform_is_windows=platform_is_windows,
        )
        return device_env_value, device_from_env, manage_mumu

    def _env_flag_is_false(self, value):
        if value is None:
            return False
        return str(value).strip().lower() in {"0", "false", "no", "off"}

    def _mumu_management_disabled_reason(self):
        if self._device_from_env:
            return "显式 FARMWATCH_DEVICE，跳过 MuMu 管理"
        if self._env_flag_is_false(self._manage_mumu_configured_value):
            configured = str(self._manage_mumu_configured_value).strip()
            return f"FARMWATCH_MANAGE_MUMU={configured}，MuMu 自动管理已禁用"
        if not self._platform_is_windows:
            return "非 Windows 平台，默认禁用 MuMu 自动管理"
        return "MuMu 自动管理已禁用"

    def adb_get_state(self):
        """获取指定设备的 ADB host 连接状态"""
        result = subprocess.run([self.ADB, "-s", self.DEVICE, "get-state"], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=10,
                                creationflags=self._SUBPROCESS_FLAGS)
        return result.stdout

    def _adb_state_is_ready(self, state_text):
        """Return True only for an exact host-ADB ``device`` state.

        Checking ``"device" in output`` also matches diagnostics such as
        ``error: device offline`` or ``no devices/emulators found``.  Those must
        continue through the reconnect path instead of being treated as ready.
        """
        if not isinstance(state_text, str):
            return False
        return any(line.strip() == "device" for line in state_text.splitlines())

    def _is_adb_transport_failure(self, message):
        """判断错误是否表示 ADB 传输层暂时掉线。"""
        normalized = str(message or "").lower()
        return any(
            marker in normalized
            for marker in (
                "device offline",
                "device not found",
                "no devices/emulators found",
                "closed",
                "connection reset",
                "transport error",
                "cannot connect",
                "failed to read response",
            )
        )

    def recover_adb_connection(self):
        """尝试恢复指定设备的 ADB 连接，并等待它真正进入 device 状态。"""
        print(f"  🔄 ADB 设备掉线，尝试恢复连接: {self.DEVICE}")

        def is_ready():
            try:
                return self._adb_state_is_ready(self.adb_get_state())
            except (OSError, subprocess.SubprocessError):
                return False

        if is_ready():
            print(f"  ✅ ADB 已自行恢复: {self.DEVICE}")
            return True

        try:
            subprocess.run(
                [self.ADB, "-s", self.DEVICE, "reconnect"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10, creationflags=self._SUBPROCESS_FLAGS,
            )
        except (OSError, subprocess.SubprocessError):
            pass

        for _ in range(self.ADB_RECOVERY_POLL_ATTEMPTS):
            self.time.sleep(self.ADB_RECOVERY_POLL_INTERVAL)
            if is_ready():
                print(f"  ✅ ADB 重连成功: {self.DEVICE}")
                return True

        if ":" in str(self.DEVICE):
            try:
                subprocess.run(
                    [self.ADB, "disconnect", self.DEVICE],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=10, creationflags=self._SUBPROCESS_FLAGS,
                )
                subprocess.run(
                    [self.ADB, "connect", self.DEVICE],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=10, creationflags=self._SUBPROCESS_FLAGS,
                )
            except (OSError, subprocess.SubprocessError):
                pass

            for _ in range(self.ADB_RECOVERY_POLL_ATTEMPTS):
                self.time.sleep(self.ADB_RECOVERY_POLL_INTERVAL)
                if is_ready():
                    print(f"  ✅ ADB 重新连接成功: {self.DEVICE}")
                    return True

        # MuMu 的 TCP endpoint 可能随实例重启/刷新而变化。普通 ADB 重连
        # 无法恢复“实例已退出或换端口”的情况；交给生命周期管理器检查并在
        # 需要时重新拉起实例，动态地址路径会同步更新全局 DEVICE。
        if self._manage_mumu:
            print(f"  🖥️ ADB 重连失败，检查/恢复 MuMu 实例: {self.MUMU_NAME}")
            if self.ensure_emulator_running(
                self.ADB_RECOVERY_EMULATOR_TIMEOUT_SEC,
                self.ADB_RECOVERY_STALE_RESTART_DELAY_SEC,
            ) and is_ready():
                print(f"  ✅ MuMu 恢复后 ADB 已就绪: {self.DEVICE}")
                return True
            print(f"  ⚠️ MuMu 恢复后 ADB 仍不可用: {self.DEVICE}")
            return False

        print(f"  ⚠️ ADB 暂未恢复: {self.DEVICE}")
        return False

    def adb_shell(self, cmd):
        """执行ADB shell命令（不经过本地 shell；管道由设备端 shell 解析）"""
        try:
            result = subprocess.run([self.ADB, "-s", self.DEVICE, "shell", cmd], capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=10,
                                    creationflags=self._SUBPROCESS_FLAGS)
        except subprocess.TimeoutExpired:
            # A MuMu/ADB transient stall must not abort the whole farming round.
            # Callers can treat an empty response as an unknown device state and
            # the next command will re-check connectivity.
            print(f"  ⚠️ ADB shell 超时(10秒)，跳过命令: {cmd}")
            return ""
        return result.stdout

    def adb_shell_result(self, cmd, timeout=15):
        """执行 ADB shell 并保留返回码、标准输出和错误输出。"""
        return subprocess.run(
            [self.ADB, "-s", self.DEVICE, "shell", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=self._SUBPROCESS_FLAGS,
        )

    def detect_resolution(self):
        """检测设备横屏分辨率"""
        import re
        out = self.adb_shell("wm size")
        sizes = re.findall(r'(\d+)x(\d+)', out)
        if sizes:
            # wm size 先打印物理尺寸，再打印实际生效的 Override size。
            w, h = map(int, sizes[-1])
            if h > w:
                w, h = h, w
            return w, h
        raise RuntimeError('无法读取设备分辨率，停止自动点击')

    def tap(self, x, y, label=""):
        """点击屏幕"""
        if label:
            print(f"  👆 tap ({x}, {y}) [{label}]")
        self.adb_shell(f"input tap {x} {y}")

    def swipe(self, x1, y1, x2, y2, duration_ms=1000):
        """滑动"""
        self.adb_shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    def ensure_emulator_running(self,
        timeout=180, stale_restart_delay=None
    ) -> bool:
        """确保目标 MuMu 实例与其 ADB 设备已经就绪。"""

        if not self._manage_mumu:
            print(f"  {self._mumu_management_disabled_reason()}")
            return True

        try:
            if self._mumu_cli_path is None:
                self._mumu_cli_path = mumu_control.find_mumu_cli()
            detected_device = mumu_control.ensure_mumu_running(
                self._mumu_cli_path,
                self.MUMU_NAME,
                self.ADB,
                timeout=timeout,
                stale_restart_delay=stale_restart_delay,
            )
            if not self._device_from_env:
                self.DEVICE = detected_device
            print(f"  ✅ MuMu 实例 {self.MUMU_NAME} 已就绪，ADB 设备: {self.DEVICE}")
            return True
        except mumu_control.MumuControlError as exc:
            print(f"  ❌ MuMu 启动失败: {exc}")
            return False

    def shutdown_emulator_for_wait(self) -> bool:
        """关闭目标 MuMu 实例；失败时保留等待流程继续运行。"""

        if not self._manage_mumu:
            print(f"  {self._mumu_management_disabled_reason()}")
            return True

        try:
            if self._mumu_cli_path is None:
                self._mumu_cli_path = mumu_control.find_mumu_cli()
            mumu_control.shutdown_mumu(self._mumu_cli_path, self.MUMU_NAME)
            print(f"  ✅ MuMu 实例 {self.MUMU_NAME} 已关闭")
            return True
        except mumu_control.MumuControlError as exc:
            print(f"  ⚠️ MuMu 关闭失败: {exc}；将继续等待")
            return False

    def check_adb_connection(self):
        """检查ADB连接状态，未连接则自动连接"""
        print("🔍 检查ADB连接...")
        result = self.adb_get_state()
        if self._adb_state_is_ready(result):
            print(f"  ✅ 设备已连接: {self.DEVICE}")
            return True

        # 自动连接
        print(f"  ⚠️ 设备未连接，正在自动连接: {self.DEVICE}")
        connect_result = subprocess.run(
            [self.ADB, "connect", self.DEVICE],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            creationflags=self._SUBPROCESS_FLAGS,
        )

        if connect_result.returncode == 0 and "connected" in connect_result.stdout.lower():
            print(f"  ✅ 自动连接成功: {self.DEVICE}")
            return True
        else:
            diagnostic = connect_result.stdout.strip() or connect_result.stderr.strip()
            if not diagnostic:
                diagnostic = f"ADB 返回码 {connect_result.returncode}"
            print(f"  ❌ 自动连接失败: {diagnostic}")
            return False
