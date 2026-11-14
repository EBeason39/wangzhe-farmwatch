"""统一 Windows 前台、后台、停止、状态和离线自检入口。"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys

from . import APP_NAME
from .config import ROOT, ConfigError, initialize_settings, load_settings


def validate_runtime():
    if os.name != 'nt':
        raise RuntimeError('当前发行版仅支持 Windows')
    if sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        raise RuntimeError('需要 Python 3.12 x64，请运行安装脚本')


def selftest(settings):
    from .app import FarmApp
    import cv2
    import numpy as np
    from farmwatch.automation import friends
    from farmwatch.vision.controls import required_assets
    app = FarmApp(settings)
    templates = [app.TEMPLATE_DIR / name for name in required_assets()]
    missing = [path.name for path in templates if not path.is_file()]
    if missing:
        raise RuntimeError('缺少识别资源：' + ', '.join(missing))
    for path in templates:
        if cv2.imread(str(path)) is None:
            raise RuntimeError(f'无法读取模板 {path.name}，请使用不含中文的可写目录')
    route = friends.load_route()
    app.get_ocr()(np.full((128, 320, 3), 255, dtype=np.uint8))
    print(f'{APP_NAME}：离线自检通过')
    print(f'Python {sys.version.split()[0]} x64；{len(templates)} 张模板；好友路线 {len(route["stops"])} 个停点；OCR 推理通过')
    print(f'项目目录：{settings.root}')
    print('未连接模拟器，未发送通知')
    return 0


def check(settings):
    from .app import FarmApp
    import farmwatch.adapters.emulator as mumu
    app = FarmApp(settings)
    if settings.manage_mumu:
        cli = Path(settings.cli) if settings.cli else mumu.find_mumu_cli()
        instance = mumu.find_mumu_instance(cli, settings.instance)
        device = mumu.instance_adb_address(instance)
        if not device:
            raise RuntimeError('模拟器尚未运行或 ADB 地址不可用；请启动指定实例后再检查')
        app.DEVICE = settings.device or device
    if not mumu.adb_is_ready(app.ADB, app.DEVICE):
        raise RuntimeError('ADB 未就绪，请检查实例和设备地址；此检查不会启动模拟器')
    result = app.adb_shell_result(f'pm path {app.GAME_PKG}')
    if result.returncode != 0 or not result.stdout.strip().startswith('package:'):
        raise RuntimeError('设备已连接，但未找到王者荣耀国服 Android 应用')
    resolution = app.detect_resolution()
    if resolution != (1280, 720):
        raise RuntimeError(f'当前分辨率 {resolution}，请设为 1280×720')
    print(f'检查通过：{app.DEVICE}，1280×720；已安装王者荣耀')
    return 0


def run(settings, args):
    from farmwatch.runtime.process import InstanceGuard, AlreadyRunning
    from farmwatch.runtime.logging import configure_logging, LineLogger
    from farmwatch.runtime.clock import Clock
    try:
        with InstanceGuard(settings.root, args.run_id) as guard:
            logger = configure_logging(settings, console=not args.background)
            original = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = LineLogger(logger), LineLogger(logger, error=True)
            code = 1
            try:
                from .app import FarmApp
                from farmwatch.integrations.push import Notifier
                import farmwatch.adapters.emulator as mumu
                if settings.manage_mumu and not settings.cli:
                    settings = replace(settings, cli=str(mumu.find_mumu_cli()))
                guard.update('starting', manage_mumu=settings.manage_mumu,
                             instance=settings.instance, cli=settings.cli)
                app = FarmApp(settings, notifier=Notifier(settings), clock=Clock(guard.should_stop))
                app.max_rounds = 1 if args.once else None
                app.status_callback = guard.update
                app._resume_wake_time = args.resume_wake_time
                app._pending_target_time = args.resume_target_time
                app._pending_target_is_absolute = args.resume_target_time is not None
                app.maintenance(force=True)
                app.time.on_tick = app.maintenance
                def stop_signal(_signum, _frame):
                    raise KeyboardInterrupt()
                signal.signal(signal.SIGINT, stop_signal)
                signal.signal(signal.SIGTERM, stop_signal)
                code = app.run_main()
                outcome = app.last_round
                guard.update('stopped' if code == 0 else 'failed', exit_code=code,
                             rounds=app.stats.rounds,
                             round_status=outcome.status.value if outcome else None,
                             click_sent=bool(outcome and outcome.click_sent),
                             harvest_confirmed=bool(outcome and outcome.harvest_confirmed))
                return code
            except Exception:
                logger.exception('启动或运行失败')
                guard.update('failed', exit_code=1)
                return 1
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                sys.stdout, sys.stderr = original
    except AlreadyRunning as exc:
        print(str(exc))
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='farmwatch', description=APP_NAME)
    parser.add_argument('action', choices=['run', 'start', 'restart', 'stop', 'status', 'check', 'selftest', 'init-config'])
    parser.add_argument('--config', type=Path, help='自定义 INI 配置路径，默认项目根目录 settings.ini')
    parser.add_argument('--once', action='store_true', help='前台仅执行一轮，随后退出并关闭托管模拟器')
    parser.add_argument('--background', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--run-id', help=argparse.SUPPRESS)
    parser.add_argument('--resume-wake-time', type=datetime.fromisoformat, help=argparse.SUPPRESS)
    parser.add_argument('--resume-target-time', type=datetime.fromisoformat, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.once and args.action != 'run':
        parser.error('--once 仅用于 run')
    if (args.resume_wake_time is not None or args.resume_target_time is not None) and args.action != 'run':
        parser.error('恢复等待参数仅用于 run')
    if args.resume_target_time is not None and args.resume_wake_time is None:
        parser.error('恢复目标时间必须同时提供唤醒时间')
    if any(value is not None and value.tzinfo is not None for value in (args.resume_wake_time, args.resume_target_time)):
        parser.error('恢复时间必须使用本地时间')
    if args.resume_target_time is not None and args.resume_target_time < args.resume_wake_time:
        parser.error('恢复目标时间不能早于唤醒时间')
    try:
        validate_runtime()
        if args.action == 'init-config':
            initialize_settings(ROOT)
            print('settings.ini 已就绪；已有非空配置保留，私人凭据不打印')
            return 0
        if args.action in {'status', 'stop'}:
            from farmwatch.runtime.process import read_state, is_running, stop
            if args.action == 'stop':
                return stop(ROOT)
            record = read_state(ROOT)
            active = is_running(ROOT, record)
            print(json.dumps({'running': active, 'phase': record.get('phase', 'not_started'),
                              'pid': record.get('pid') if active else None,
                              'exit_code': record.get('exit_code'), 'wake_time': record.get('wake_time'),
                              'target_time': record.get('target_time')}, ensure_ascii=False))
            return 0
        settings = load_settings(args.config)
        if args.action == 'start':
            from farmwatch.runtime.process import start_background
            return start_background(ROOT, config_path=args.config)
        if args.action == 'restart':
            from farmwatch.runtime.process import restart_background
            return restart_background(ROOT, config_path=args.config, wake_ahead_min=settings.wake_ahead_min)
        if args.action == 'selftest':
            return selftest(settings)
        if args.action == 'check':
            return check(settings)
        return run(settings, args)
    except (ConfigError, RuntimeError, OSError, ImportError, subprocess.SubprocessError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
