"""Helpers for locating and querying the MuMu command-line utility."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable


_NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

DEFAULT_MUMU_CLI_PATHS = (
    r"D:\Program Files\NetEase\MuMu\nx_main\mumu-cli.exe",
    r"C:\Program Files\NetEase\MuMu\nx_main\mumu-cli.exe",
)


class MumuControlError(RuntimeError):
    """Raised when the MuMu CLI cannot be found, run, or parsed."""


def find_mumu_cli(env=None, candidates=None) -> Path:
    """Return the first existing MuMu CLI executable.

    ``FARMWATCH_MUMU_CLI`` is an explicit override.  When it is present, only
    that path is checked; the normal candidate list is not used as a
    fallback.
    """

    environment = os.environ if env is None else env
    if "FARMWATCH_MUMU_CLI" in environment:
        configured = environment["FARMWATCH_MUMU_CLI"]
        try:
            path = Path(configured)
        except (TypeError, ValueError):
            path = Path(str(configured))
        if path.is_file():
            return path
        raise MumuControlError(
            f"找不到 MuMu CLI，已检查路径：{path}"
        )

    paths: Iterable[Any] = (
        DEFAULT_MUMU_CLI_PATHS if candidates is None else candidates
    )
    checked: list[str] = []
    for candidate in paths:
        path = Path(candidate)
        checked.append(str(path))
        if path.is_file():
            return path

    details = "、".join(checked) if checked else "（无）"
    raise MumuControlError(f"找不到 MuMu CLI，已检查路径：{details}")


def _as_diagnostic(value: Any) -> str:
    """Convert subprocess diagnostic data to readable text."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _diagnostics(*values: Any) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in values:
        part = _as_diagnostic(value)
        if part and part not in seen:
            seen.add(part)
            parts.append(part)
    return "；".join(parts)


def _output_summary(value: Any, limit: int = 200) -> str:
    """Return a bounded summary suitable for an exception message."""

    text = _as_diagnostic(value)
    return text[:limit] + ("…" if len(text) > limit else "")


def run_mumu_cli(cli_path, *args, timeout=30) -> str:
    """Run MuMu CLI with an argument list and return standard output."""

    command = [str(cli_path), *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW_FLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _diagnostics(
            getattr(exc, "stderr", None),
            getattr(exc, "stdout", None),
            getattr(exc, "output", None),
        )
        suffix = f"：{detail}" if detail else f"：{exc}"
        raise MumuControlError(f"MuMu CLI 执行超时{suffix}") from exc
    except OSError as exc:
        raise MumuControlError(f"无法执行 MuMu CLI：{exc}") from exc

    if completed.returncode != 0:
        detail = _diagnostics(completed.stderr, completed.stdout)
        suffix = f"：{detail}" if detail else ""
        raise MumuControlError(
            f"MuMu CLI 返回非零状态码 {completed.returncode}{suffix}"
        )
    return completed.stdout


def list_mumu_instances(cli_path, timeout=30) -> list[dict[str, Any]]:
    """List MuMu instances reported by ``mumu-cli info -v all``.

    The CLI returns an object keyed by instance id.  Only dictionary values
    are instance records; other metadata values are ignored.
    """

    output = run_mumu_cli(cli_path, "info", "-v", "all", timeout=timeout)
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        summary = _output_summary(output)
        raise MumuControlError(
            f"MuMu CLI 输出不是合法 JSON：{exc}；原始输出摘要：{summary}"
        ) from exc

    if not isinstance(payload, dict):
        raise MumuControlError("MuMu CLI JSON 顶层必须是对象")
    return [value for value in payload.values() if isinstance(value, dict)]


def find_mumu_instance(cli_path, name: str, timeout=30) -> dict[str, Any]:
    """Find an instance by exact name, or explain which names are present."""

    instances = list_mumu_instances(cli_path, timeout=timeout)
    for instance in instances:
        if instance.get("name") == name:
            return instance

    available = [str(instance.get("name", "<未命名>")) for instance in instances]
    names = "、".join(available) if available else "（无）"
    raise MumuControlError(
        f"未找到 MuMu 实例 {name!r}，可用实例名称：{names}"
    )


def instance_adb_address(instance: Mapping[str, Any]) -> str | None:
    """Return an instance's ADB host/port as ``host:port`` when available."""

    if not isinstance(instance, Mapping):
        return None
    host = instance.get("adb_host_ip")
    port = instance.get("adb_port")
    if host is None or (isinstance(host, str) and not host.strip()):
        return None
    if port is None or (isinstance(port, str) and not port.strip()):
        return None
    return f"{host}:{port}"


def adb_is_ready(adb_path, device: str, timeout=10) -> bool:
    """Return whether ADB reports the selected device as ready."""

    try:
        completed = subprocess.run(
            [str(adb_path), "-s", device, "get-state"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "device"


def adb_connect(adb_path, device: str, timeout=10) -> bool:
    """Ask the ADB daemon to register a host/port device.

    MuMu may expose a newly launched instance on a dynamic TCP endpoint
    without registering that endpoint in the local ADB daemon.  ``connect``
    is deliberately best-effort here: callers can continue polling
    ``adb_is_ready`` when the daemon is still starting or when the command
    fails transiently.
    """

    try:
        completed = subprocess.run(
            [str(adb_path), "connect", device],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _process_started(instance: Mapping[str, Any]) -> bool:
    """Validate and return the MuMu process-started state."""

    if not isinstance(instance, Mapping):
        raise MumuControlError("MuMu 实例状态不是对象")
    value = instance.get("is_process_started")
    if value is not True and value is not False:
        raise MumuControlError(
            "MuMu 实例状态缺少有效的 is_process_started 布尔值"
        )
    return value


def _instance_index(instance: Mapping[str, Any]) -> str:
    """Return a non-empty instance index or raise a diagnostic error."""

    if not isinstance(instance, Mapping):
        raise MumuControlError("MuMu 实例状态不是对象，无法读取 index")
    value = instance.get("index")
    if value is None or not str(value).strip():
        raise MumuControlError("MuMu 实例缺少有效的 index")
    return str(value)


def _instance_started(instance: Mapping[str, Any]) -> bool:
    """Return whether MuMu reports that an instance fully finished starting."""

    return (
        _process_started(instance)
        and instance.get("is_android_started") is True
        and instance.get("player_state") == "start_finished"
    )


def _remaining_time(deadline, monotonic_func, timeout_message: str) -> float:
    """Return remaining budget, raising once the lifecycle deadline expires."""

    remaining = deadline - monotonic_func()
    if remaining <= 0:
        raise MumuControlError(timeout_message)
    return remaining


def _adb_ready_with_optional_connect(
    adb_path,
    device: str,
    *,
    deadline,
    timeout_message: str,
    connect_attempted: bool,
    monotonic_func=time.monotonic,
) -> tuple[bool, bool]:
    """Check ADB readiness and register the endpoint at most once.

    Every blocking ADB operation receives only the lifecycle budget still
    available immediately before it starts.  The returned flag lets callers
    carry the single ``adb connect`` attempt across polling iterations.
    """

    remaining = _remaining_time(deadline, monotonic_func, timeout_message)
    if adb_is_ready(adb_path, device, timeout=min(10, remaining)):
        return True, connect_attempted

    if connect_attempted:
        return False, True

    remaining = _remaining_time(deadline, monotonic_func, timeout_message)
    adb_connect(adb_path, device, timeout=min(10, remaining))
    connect_attempted = True

    remaining = _remaining_time(deadline, monotonic_func, timeout_message)
    ready = adb_is_ready(adb_path, device, timeout=min(10, remaining))
    return ready, connect_attempted


def ensure_mumu_running(
    cli_path,
    name: str,
    adb_path,
    timeout=180,
    poll_interval=2,
    max_launch_attempts=3,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    stale_restart_delay=None,
) -> str:
    """Launch a MuMu instance when needed and wait for Android and ADB.

    ``stale_restart_delay`` handles a MuMu edge case where the host process
    remains alive but Android never finishes starting.  After the delay, the
    stale process is shut down and launched once so the remaining lifecycle
    budget can still produce a usable ADB endpoint.
    """

    deadline = monotonic_func() + timeout
    timeout_message = (
        f"MuMu 实例 {name!r} 在 {timeout} 秒内未完成启动或 ADB 未就绪"
    )
    adb_connect_attempted = False
    stale_restart_deadline = None
    stale_restart_attempted = False

    remaining = _remaining_time(deadline, monotonic_func, timeout_message)
    instance = find_mumu_instance(
        cli_path, name, timeout=min(30, remaining)
    )
    address = instance_adb_address(instance)
    if _instance_started(instance) and address:
        ready, adb_connect_attempted = _adb_ready_with_optional_connect(
            adb_path,
            address,
            deadline=deadline,
            timeout_message=timeout_message,
            connect_attempted=adb_connect_attempted,
            monotonic_func=monotonic_func,
        )
        if ready:
            return address
        if stale_restart_delay is not None:
            stale_restart_deadline = (
                monotonic_func() + stale_restart_delay
            )

    if not _process_started(instance):
        for attempt in range(1, max_launch_attempts + 1):
            remaining = _remaining_time(deadline, monotonic_func, timeout_message)
            index = _instance_index(instance)
            try:
                run_mumu_cli(
                    cli_path,
                    "control",
                    "-v",
                    index,
                    "launch",
                    timeout=min(30, remaining),
                )
                break
            except MumuControlError as exc:
                remaining = _remaining_time(deadline, monotonic_func, timeout_message)
                instance = find_mumu_instance(
                    cli_path,
                    name,
                    timeout=min(30, remaining),
                )
                if _process_started(instance):
                    break
                if attempt == max_launch_attempts:
                    raise MumuControlError(
                        f"MuMu 实例 {name!r} 启动命令连续失败 "
                        f"{max_launch_attempts} 次：{exc}"
                    ) from exc
                remaining = _remaining_time(deadline, monotonic_func, timeout_message)
                sleep_func(min(poll_interval, remaining))
        else:
            raise MumuControlError(
                f"MuMu 实例 {name!r} 启动命令连续失败 "
                f"{max_launch_attempts} 次"
            )

    while True:
        remaining = _remaining_time(deadline, monotonic_func, timeout_message)
        instance = find_mumu_instance(
            cli_path,
            name,
            timeout=min(30, remaining),
        )
        address = instance_adb_address(instance)
        if _instance_started(instance) and address:
            ready, adb_connect_attempted = _adb_ready_with_optional_connect(
                adb_path,
                address,
                deadline=deadline,
                timeout_message=timeout_message,
                connect_attempted=adb_connect_attempted,
                monotonic_func=monotonic_func,
            )
            if ready:
                return address

        if (
            stale_restart_delay is not None
            and not stale_restart_attempted
            and _process_started(instance)
            and not _instance_started(instance)
        ):
            if stale_restart_deadline is None:
                stale_restart_deadline = (
                    monotonic_func() + stale_restart_delay
                )
            elif monotonic_func() >= stale_restart_deadline:
                stale_restart_attempted = True
                index = _instance_index(instance)
                remaining = _remaining_time(
                    deadline, monotonic_func, timeout_message
                )
                run_mumu_cli(
                    cli_path,
                    "control",
                    "-v",
                    index,
                    "shutdown",
                    timeout=min(30, remaining),
                )
                while True:
                    remaining = _remaining_time(
                        deadline, monotonic_func, timeout_message
                    )
                    instance = find_mumu_instance(
                        cli_path,
                        name,
                        timeout=min(30, remaining),
                    )
                    if not _process_started(instance):
                        break
                    remaining = _remaining_time(
                        deadline, monotonic_func, timeout_message
                    )
                    sleep_func(min(poll_interval, remaining))

                remaining = _remaining_time(deadline, monotonic_func, timeout_message)
                index = _instance_index(instance)
                run_mumu_cli(
                    cli_path,
                    "control",
                    "-v",
                    index,
                    "launch",
                    timeout=min(30, remaining),
                )
                stale_restart_deadline = (
                    monotonic_func() + stale_restart_delay
                )
                continue

        remaining = _remaining_time(deadline, monotonic_func, timeout_message)
        sleep_func(min(poll_interval, remaining))


def shutdown_mumu(
    cli_path,
    name: str,
    timeout=60,
    poll_interval=2,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> bool:
    """Shut down a MuMu instance and wait until its process stops."""

    deadline = monotonic_func() + timeout
    timeout_message = f"MuMu 实例 {name!r} 在 {timeout} 秒内未停止"

    remaining = _remaining_time(deadline, monotonic_func, timeout_message)
    instance = find_mumu_instance(
        cli_path, name, timeout=min(30, remaining)
    )
    if not _process_started(instance):
        return True

    remaining = _remaining_time(deadline, monotonic_func, timeout_message)
    index = _instance_index(instance)
    run_mumu_cli(
        cli_path,
        "control",
        "-v",
        index,
        "shutdown",
        timeout=min(30, remaining),
    )

    while True:
        remaining = _remaining_time(deadline, monotonic_func, timeout_message)
        instance = find_mumu_instance(
            cli_path,
            name,
            timeout=min(30, remaining),
        )
        if not _process_started(instance):
            return True
        remaining = _remaining_time(deadline, monotonic_func, timeout_message)
        sleep_func(min(poll_interval, remaining))


__all__ = [
    "DEFAULT_MUMU_CLI_PATHS",
    "MumuControlError",
    "find_mumu_cli",
    "run_mumu_cli",
    "list_mumu_instances",
    "find_mumu_instance",
    "instance_adb_address",
    "adb_is_ready",
    "adb_connect",
    "ensure_mumu_running",
    "shutdown_mumu",
]
