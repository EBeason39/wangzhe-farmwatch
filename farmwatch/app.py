"""组装配置、设备动作、识别和业务流程；每个实例独立持有状态。"""
from __future__ import annotations
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
import cv2
import numpy as np
from .config import Settings
from farmwatch.adapters.android import DeviceActions, resolve_adb
from farmwatch.domain.crops import CropScheduling
from farmwatch.vision.screen import Recognition
from farmwatch.automation.updates import GameUpdates
from farmwatch.automation.cycle import FarmWorkflow
from farmwatch.domain.results import Stats, ScreenshotCaptureError
from farmwatch.runtime.clock import Clock
from farmwatch.vision.controls import FRAME_SIZE, CONTROLS


class FarmApp(DeviceActions, CropScheduling, Recognition, GameUpdates, FarmWorkflow):
    ScreenshotCaptureError = ScreenshotCaptureError

    def __init__(self, settings: Settings | None = None, *, notifier=None, clock=None):
        self.settings = settings or Settings()
        self.__name__ = 'farmwatch'
        self.os, self.sys, self.subprocess = os, sys, subprocess
        self.cv2, self.np = cv2, np
        self.datetime = datetime
        self.time = clock or Clock()
        self.SCRIPT_DIR = Path(self.settings.root).resolve()
        self.DATA_DIR = self.SCRIPT_DIR / 'data'
        self.TEMPLATE_DIR = self.SCRIPT_DIR / 'resources' / 'ui' / '1280x720'
        self.SCREENSHOT_PATH = str(self.DATA_DIR / 'current.png')
        self.SCREENSHOT_ARCHIVE_DIR = self.DATA_DIR / 'farm_screenshots'
        self._DATETIME_TYPE = self.datetime
        self._SUBPROCESS_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        self.SCREENSHOT_ARCHIVE_RETENTION_SECONDS = 72 * 60 * 60
        self.SCREENSHOT_ARCHIVE_PREFIX = 'device_'
        self.SCREENSHOT_MAX_ATTEMPTS = 3
        self.SCREENSHOT_RETRY_INTERVAL = 0.2
        self.ADB_RECOVERY_POLL_ATTEMPTS = 5
        self.ADB_RECOVERY_POLL_INTERVAL = 1.0
        self.ADB_RECOVERY_EMULATOR_TIMEOUT_SEC = 300
        self.ADB_RECOVERY_STALE_RESTART_DELAY_SEC = 150
        self.GAME_PKG = 'com.tencent.tmgp.sgame'
        self.GAME_ACT = f'{self.GAME_PKG}/com.tencent.tmgp.sgame.SGameActivity'
        self.BASE_W, self.BASE_H = FRAME_SIZE
        self.LOGIN_FLOW_TIMEOUT = 120.0
        self.LOGIN_POLL_INTERVAL = 2.0
        self.LOGIN_TRANSITION_TIMEOUT = 8.0
        self.LOGIN_MAX_STATE_ATTEMPTS = 3
        self.LOGIN_UNKNOWN_LIMIT = 10
        self.LOGIN_QQ_AGREE_SAME_CENTER_TOLERANCE_PX = 12.0
        self.LOGIN_CONTROLS = {name: {'threshold': .80, 'roi': spec.region}
                               for name, spec in CONTROLS.items() if name.startswith('account.')}
        self.JOYSTICK_CENTER = (160, 486)
        self.JOYSTICK_RADIUS = 200
        self.statue_path = {'center': (160, 486), 'angle': 120, 'distance': 200, 'duration': 1500}
        self.FARM_ACTION_CONTROL = 'farm.action'
        self.FARM_ACTION_THRESHOLD = 0.72
        self.FARM_ACTION_ROI = (0.45, 0.35, 0.85, 0.75)
        self.CYCLE_FILE = str(self.DATA_DIR / 'crop_cycle.json')
        self.MAX_CROP_CYCLE_MIN = 1920
        self.SUPPORTED_CROP_CYCLES_MIN = (60, 480, 960, 1920)
        self.MAX_SCHEDULE_WATERINGS = 128
        self.CROP_RECORD_SCHEMA_VERSION = 3
        self.OCR_REMAINING_TOLERANCE_MIN = 5.0
        self.UNKNOWN_CROP_NAMES = {'作物', '未知作物'}
        self._ocr_engine = None
        self.KNOWN_CROP_NAMES = {'番茄', '洋葱', '小麦', '土豆', '胡萝卜', '白菜', '玉米', '南瓜', '草莓', '西瓜', '辣椒', '茄子', '黄瓜', '大豆', '荞麦', '红枣', '脆薯饼', '原叶鲜奶茶', '菠萝', '橘子', '梨子', '甘蔗', '樱桃', '柠檬', '杏子'}
        self.CROP_NAME_ROI = (0.015, 0.165, 0.165, 0.215)
        self.CROP_NAME_OCR_SCALE = 3
        self.CROP_VARIANT_PREFIX = '变异'
        self.CROP_VARIANT_SUFFIXES = ('（变异）', '(变异)', '变异')
        self.CROP_NAME_UI_WORDS = {'农场', '满级', '成熟', '升级', '可升级', '农场经验', '可收获', '已成熟', '等级', '水分', '今天', '明天', '后天', '变异'}
        self.GAME_UPDATE_DIR = f'/sdcard/Android/data/{self.GAME_PKG}/files/iips_download'
        self._pending_target_time = None
        self._pending_target_is_absolute = False
        self._resume_wake_time = None
        self.ADB = resolve_adb(self.settings)
        self.DEVICE = self.settings.device or '127.0.0.1:16704'
        self._device_env_value = self.settings.device or None
        self._device_from_env = bool(self.settings.device)
        self._manage_mumu = self.settings.manage_mumu
        self._manage_mumu_configured_value = '1' if self._manage_mumu else '0'
        self._platform_is_windows = os.name == 'nt'
        self.MUMU_NAME = self.settings.instance
        self._mumu_cli_path = Path(self.settings.cli) if self.settings.cli else None
        self.WAKE_AHEAD_MIN = self.settings.wake_ahead_min
        self.HARVEST_DELAY_SEC = self.settings.harvest_delay_sec
        self.GAME_UPDATE_TIMEOUT_MIN = self.settings.update_timeout_min
        self.GAME_UPDATE_POLL_SEC = self.settings.update_poll_sec
        self.SCREENSHOT_ARCHIVE_RETENTION_SECONDS = self.settings.screenshots_retention_hours * 3600
        self.stats = Stats()
        self._notify = notifier
        self._last_archive_cleanup = float('-inf')
        self._template_cache = {}
        self._frame_cache = {}
        self._control_ocr_cache = {}
        self.last_round = None
        self.max_rounds = None
        self.status_callback = lambda phase, **fields: None
