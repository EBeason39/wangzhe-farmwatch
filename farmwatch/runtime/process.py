"""Windows 单实例和停止协议：命名互斥量、停止事件、PID 创建时间。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x100000
PROCESS_TERMINATE = 0x0001


class AlreadyRunning(RuntimeError):
    pass


def kernel():
    if os.name != 'nt':
        raise RuntimeError('FarmWatch 当前仅支持 Windows')
    dll = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateMutexW': ([wt.LPVOID, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
        'ReleaseMutex': ([wt.HANDLE], wt.BOOL),
        'CreateEventW': ([wt.LPVOID, wt.BOOL, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
        'OpenEventW': ([wt.DWORD, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
        'SetEvent': ([wt.HANDLE], wt.BOOL),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
        'WaitForSingleObject': ([wt.HANDLE, wt.DWORD], wt.DWORD),
        'OpenProcess': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        'GetProcessTimes': ([wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4, wt.BOOL),
        'QueryFullProcessImageNameW': ([wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'TerminateProcess': ([wt.HANDLE, wt.UINT], wt.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = args, result
    return dll


def root_key(root):
    return hashlib.sha256(str(Path(root).resolve()).casefold().encode('utf-8')).hexdigest()[:24]


def state_path(root):
    return Path(root) / 'tmp' / 'farmwatch_process.json'


def read_state(root):
    try:
        data = json.loads(state_path(root).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(root, data):
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f'.process-{uuid.uuid4().hex}.tmp')
    try:
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        # Windows 状态读取/杀毒扫描会短暂禁止替换；保留原子写入，仅有界重试。
        for attempt in range(5):
            try:
                pending.replace(path)
                break
            except PermissionError as exc:
                if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 4:
                    raise
                time.sleep(.02 * (attempt + 1))
    finally:
        pending.unlink(missing_ok=True)


def _identity(dll, handle):
    created, exited, kernel_time, user_time = [wt.FILETIME() for _ in range(4)]
    if not dll.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
        return None
    size = wt.DWORD(32768)
    image = ctypes.create_unicode_buffer(size.value)
    if not dll.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
        return None
    return {'created': (created.dwHighDateTime << 32) | created.dwLowDateTime,
            'executable': str(Path(image.value).resolve())}


def open_verified_process(root, record, *, terminate=False):
    """返回绑定同一个内核进程对象的句柄，校验失败则绝不终止进程。"""
    if record.get('root') != str(Path(root).resolve()):
        return None
    pid = record.get('pid')
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
        return None
    dll = kernel()
    access = PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if terminate:
        access |= PROCESS_TERMINATE
    handle = dll.OpenProcess(access, False, pid)
    if not handle:
        return None
    identity = _identity(dll, handle)
    active = dll.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    if (not active or not identity or identity['created'] != record.get('created')
            or identity['executable'].casefold() != str(record.get('executable', '')).casefold()):
        dll.CloseHandle(handle)
        return None
    return handle


def is_running(root, record=None):
    handle = open_verified_process(root, record if record is not None else read_state(root))
    if handle:
        kernel().CloseHandle(handle)
        return True
    return False


class InstanceGuard:
    def __init__(self, root, run_id=None):
        self.root = Path(root).resolve()
        self.run_id = run_id or uuid.uuid4().hex
        self.dll = kernel()
        self.mutex = self.event = None
        self.record = {}

    def __enter__(self):
        name = 'Local\\FarmWatch_' + root_key(self.root)
        ctypes.set_last_error(0)
        self.mutex = self.dll.CreateMutexW(None, True, name)
        error = ctypes.get_last_error()
        if not self.mutex:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS: another worker owns the project.
            self.dll.CloseHandle(self.mutex)
            self.mutex = None
            raise AlreadyRunning('此项目已有前台或后台任务运行')
        try:
            self.event = self.dll.CreateEventW(None, True, False, name + '_stop')
            if not self.event:
                raise ctypes.WinError(ctypes.get_last_error())
            handle = self.dll.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, os.getpid())
            try:
                identity = _identity(self.dll, handle) if handle else None
            finally:
                if handle:
                    self.dll.CloseHandle(handle)
            if not identity:
                raise RuntimeError('无法读取本进程身份，停止启动')
            self.record = dict(identity, root=str(self.root), pid=os.getpid(), run_id=self.run_id,
                               started=datetime.now().isoformat(), phase='starting')
            self.update('starting')
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def update(self, phase, **fields):
        self.record.update(fields, phase=phase, updated=datetime.now().isoformat())
        write_state(self.root, self.record)

    def should_stop(self):
        return bool(self.event and self.dll.WaitForSingleObject(self.event, 0) == WAIT_OBJECT_0)

    def __exit__(self, kind, value, tb):
        try:
            if self.record and self.record.get('phase') not in {'stopped', 'failed'}:
                self.update('failed' if kind else 'stopped', exit_code=1 if kind else 0)
        finally:
            if self.event:
                self.dll.CloseHandle(self.event)
                self.event = None
            if self.mutex:
                self.dll.ReleaseMutex(self.mutex)
                self.dll.CloseHandle(self.mutex)
                self.mutex = None


def start_background(root, *, config_path=None, resume_wake_time=None, resume_target_time=None):
    root = Path(root).resolve()
    if is_running(root):
        print(f"已运行，PID={read_state(root)['pid']}")
        return 0
    interpreter = root / 'venv' / 'Scripts' / 'pythonw.exe'
    if not interpreter.is_file():
        interpreter = Path(sys.executable)
    run_id = uuid.uuid4().hex
    command = [str(interpreter), '-B', '-X', 'utf8', '-u', '-m', 'farmwatch',
               'run', '--background', '--run-id', run_id]
    if config_path is not None:
        command.extend(['--config', str(Path(config_path).resolve())])
    if resume_wake_time is not None:
        command.extend(['--resume-wake-time', resume_wake_time.isoformat()])
        if resume_target_time is not None:
            command.extend(['--resume-target-time', resume_target_time.isoformat()])
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    bootstrap = logs / f'farmwatch_bootstrap_{datetime.now():%Y%m%d_%H%M%S}_{run_id[:8]}.log'
    with bootstrap.open('wb') as output:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                                 close_fds=True)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        record = read_state(root)
        if record.get('run_id') == run_id:
            if record.get('phase') == 'failed':
                print(f'启动失败，请查看 {logs / "farmwatch.log"}')
                return 1
            if is_running(root, record):
                print(f"后台任务已创建，状态={record['phase']}，PID={record['pid']}")
                print(f'日志：{logs / "farmwatch.log"}')
                return 0
        elif is_running(root, record):
            print(f"另一个入口已经启动任务，PID={record['pid']}")
            return 0
        if child.poll() is not None:
            print(f'后台进程提前退出，请查看 {bootstrap}')
            return 1
        time.sleep(0.1)
    print(f'未收到启动确认，请查看 {bootstrap}')
    return 1


def stop(root, *, timeout=30):
    record = read_state(root)
    dll = kernel()
    handle = open_verified_process(root, record, terminate=True)
    if not handle:
        print('没有匹配的 FarmWatch 进程；未终止任何其他进程')
        return 0
    try:
        name = 'Local\\FarmWatch_' + root_key(root) + '_stop'
        event = dll.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
        if not event:
            print('无法确认停止事件，未终止进程')
            return 1
        try:
            if not dll.SetEvent(event):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            dll.CloseHandle(event)
        print('已请求停止，等待当前操作退出…')
        wait = dll.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
        if wait == WAIT_TIMEOUT:
            if not dll.TerminateProcess(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            dll.WaitForSingleObject(handle, 5000)
            if record.get('manage_mumu') and record.get('cli'):
                from farmwatch.adapters.emulator import shutdown_mumu
                shutdown_mumu(Path(record['cli']), record['instance'])
            record.update(phase='stopped', exit_code=1, forced=True, updated=datetime.now().isoformat())
            write_state(root, record)
            print('等待超时，已终止通过身份校验的任务')
        elif wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        else:
            print('任务已停止')
        return 0
    finally:
        dll.CloseHandle(handle)


def waiting_resume_plan(record, *, wake_ahead_min=5):
    """停止旧进程前保存绝对等待计划；新版明确的空目标不会误加提前量。"""
    if record.get('phase') != 'waiting':
        return None, None
    try:
        wake = datetime.fromisoformat(record['wake_time'])
        target_value = record.get('target_time')
        if 'target_time' not in record:
            # 兼容尚未保存目标时间的旧后台；实际重启前可按日志核对提前量。
            target = wake + timedelta(minutes=wake_ahead_min)
        else:
            target = datetime.fromisoformat(target_value) if target_value is not None else None
        if wake.tzinfo is not None or target is not None and target.tzinfo is not None:
            raise ValueError('aware timestamps are not local schedule timestamps')
        if target is not None and target < wake:
            raise ValueError('target precedes wake')
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError('等待计划无效，未停止当前后台') from exc
    return wake, target


def restart_background(root, *, config_path=None, wake_ahead_min=5):
    """正常停止后加载新代码，并恢复等待中的唤醒/务农目标。"""
    root = Path(root).resolve()
    wake, target = waiting_resume_plan(read_state(root), wake_ahead_min=wake_ahead_min)
    if wake is not None:
        print(f'保留唤醒时间：{wake:%Y-%m-%d %H:%M:%S}')
        if target is not None:
            print(f'保留操作目标：{target:%Y-%m-%d %H:%M:%S}')
    code = stop(root)
    if code != 0:
        return code
    return start_background(root, config_path=config_path,
                            resume_wake_time=wake, resume_target_time=target)
