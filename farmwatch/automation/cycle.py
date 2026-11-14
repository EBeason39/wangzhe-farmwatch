"""务农循环：进入、定位、执行、识别、安排下一次运行。"""

from __future__ import annotations
import sys
import subprocess
from datetime import timedelta
import cv2
import numpy as np
from farmwatch.domain.results import ScreenshotCaptureError
from farmwatch.automation import friends
import logging
from farmwatch.domain.results import RoundResult, RoundStatus


class FarmWorkflow:
    def signal_handler(self, sig, frame):
        print("\n\n⚠️ 收到终止信号，正在退出...")
        self.stats.summary()
        sys.exit(0)

    def notify_round(self, round_num, harvested, plan=None, wake_time=None):
        """每轮一键务农成功后推送通知，正文为作物识别/10 生成的浇水计划。"""
        if self._notify is None:
            return
        try:
            if harvested:
                title = f"🌾 王者农场 第{round_num}轮收获成功"
            else:
                title = f"💧 王者农场 第{round_num}轮浇水完成"

            lines = []
            if harvested and self.stats.last_detail:
                lines.append(f"**本轮收获**: {self.stats.last_detail}")

            if isinstance(plan, dict) and self._is_datetime(plan.get("mature_time")):
                cycle_min = plan.get("plant_cycle_min")
                crop = plan.get("crop_name") or "作物"
                if cycle_min:
                    lines.append(
                        f"**🌾 当前作物**: {crop}；周期 {cycle_min // 60} 小时"
                        f"（{cycle_min} 分钟）"
                    )
                else:
                    lines.append(f"**🌾 当前作物**: {crop}")

                maintenance = plan.get("water_maintenance_min")
                cooldown = plan.get("water_cooldown_min")
                if maintenance is not None and cooldown is not None:
                    lines.append(
                        f"**💧 水分维持** {maintenance:.1f} 分钟；"
                        f"冷却 {cooldown:.1f} 分钟"
                    )

                elapsed = plan.get("elapsed_min")
                count = plan.get("watering_count")
                if elapsed is not None:
                    extra = f"；后续浇水 {count} 次" if count is not None else ""
                    lines.append(f"**🎯 最短成熟耗时**: {elapsed:.1f} 分钟{extra}")

                watering_times = plan.get("watering_times") or []
                if watering_times:
                    schedule = "\n".join(
                        f"- 第{index}次：{t.strftime('%H:%M:%S')}"
                        for index, t in enumerate(watering_times, start=2)
                    )
                    lines.append(f"**⏰ 浇水计划**:\n{schedule}")
                    if plan.get("exact_maturity"):
                        lines.append("✅ 最后一次浇水后正好成熟")

                lines.append(
                    "**🌾 预计成熟/收获**: "
                    f"{plan['mature_time'].strftime('%m-%d %H:%M')}"
                )
            else:
                lines.append("本轮未生成浇水计划（作物已成熟或时间识别失败），将立即/稍后重试。")

            if self._is_datetime(wake_time):
                lines.append(f"**🔔 下次唤醒**: {wake_time.strftime('%m-%d %H:%M:%S')}")

            self._notify.send_push(title, "\n\n".join(lines))
        except Exception as e:
            print(f"  [notify] 轮次推送异常(已忽略): {e}")

    def _is_datetime(self, value):
        return isinstance(value, self._DATETIME_TYPE)

    def notify_error_exit(self, reason):
        """脚本异常/失败退出时推送告警；任何异常都不影响退出流程。"""
        if self._notify is None:
            return
        try:
            elapsed_hours = (self.datetime.now() - self.stats.start_time).total_seconds() / 3600
            desp = (
                f"**原因**: {reason}\n\n"
                f"**运行轮数**: {self.stats.rounds}\n\n"
                f"**累计收获**: {self.stats.harvests} 次\n\n"
                f"**已运行**: {elapsed_hours:.1f} 小时\n\n"
                f"**退出时间**: {self.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self._notify.send_push("❌ 王者农场脚本异常退出", desp)
        except Exception as e:
            print(f"  [notify] 退出推送异常(已忽略): {e}")

    def report_device_state(self):
        """有可靠前台信息时直接判断，仅传输失败或空结果才重试。"""
        print("\n[FarmWatch / 设备状态] 检测状态...")
        for attempt in range(3):
            for command, markers in (
                ('dumpsys activity activities', ('ResumedActivity', 'topResumedActivity')),
                ('dumpsys window', ('mCurrentFocus', 'mFocusedApp')),
                ('dumpsys activity top', ('ACTIVITY',)),
            ):
                output = self.adb_shell(command)
                active = [line for line in output.splitlines() if any(marker in line for marker in markers)
                          and 'null' not in line.lower()]
                if not active:
                    continue
                if any(self.GAME_PKG in line for line in active):
                    self.adb_shell(f'am force-stop {self.GAME_PKG}')
                    self.time.sleep(5)
                    return True
                print('  王者荣耀不在前台')
                return False
            if attempt < 2:
                self.time.sleep(2)
        print('  ⚠️ 无法确认前台状态，交由启动步骤检查设备')
        return False

    def launch_game(self):
        """启动游戏: 启动游戏，并确认进程/前台 Activity 确实存在。"""
        print("\n[FarmWatch / 启动游戏] 启动游戏...")
        # 设备刚唤醒时 Android 可能仍在启动，先给系统一个短暂的稳定时间。
        # ``get-state`` is an ADB host command, not a command available inside
        # Android's ``/system/bin/sh``.  Do not route it through ``adb shell``.
        state = subprocess.run(
            [self.ADB, "-s", self.DEVICE, "get-state"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=self._SUBPROCESS_FLAGS,
        )
        if state.returncode != 0 or not self._adb_state_is_ready(state.stdout):
            print(f"  ❌ ADB 设备未就绪: {(state.stderr or state.stdout).strip()}")
            return False

        launch_commands = [
            f"am start -W -n {self.GAME_ACT}",
            f"monkey -p {self.GAME_PKG} 1",
            # MuMu 桌面图标是最后的兜底方式，适用于游戏自行接管启动流程的版本。
            "input tap 963 151",
        ]
        for index, command in enumerate(launch_commands, 1):
            result = self.adb_shell_result(command, timeout=30)
            output = " ".join(filter(None, [(result.stdout or "").strip(), (result.stderr or "").strip()]))
            if result.returncode != 0:
                print(f"  ⚠️ 启动方式 {index} 返回失败: {output}")
                continue
            if output:
                print(f"  ℹ️ 启动返回: {output[:240]}")

            print("  ⏳ 等待游戏进程启动...")
            deadline = self.time.monotonic() + 15
            while self.time.monotonic() < deadline:
                pid = self.adb_shell(f"pidof {self.GAME_PKG}").strip()
                focus = self.adb_shell("dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'")
                top = self.adb_shell("dumpsys activity top | grep ACTIVITY")
                if pid or self.GAME_PKG in focus or self.GAME_PKG in top:
                    print(f"  ✅ 游戏已启动 (pid={pid or '前台Activity'})")
                    print("  ⏳ 等待40秒，等待启动弹窗出现...")
                    self.time.sleep(40)
                    return True
                self.time.sleep(2)
            print(f"  ⚠️ 启动方式 {index} 未检测到游戏，尝试下一种方式")

        print("  ❌ 游戏启动失败：进程和前台 Activity 均未出现")
        return False

    def dismiss_startup_overlays(self):
        """启动弹窗: 启动后关闭弹窗（逻辑与大厅弹窗一致）"""
        print("\n[FarmWatch / 启动弹窗] 关闭启动弹窗...")

        miss_count = 0
        for i in range(10):  # 最多处理10个弹窗
            self.screenshot(self.SCREENSHOT_PATH)
            result = self.locate_control("dialog.close", self.SCREENSHOT_PATH, 0.9)

            if result:
                x, y = result["x"], result["y"]
                # 位置校验：弹窗关闭按钮应在屏幕中央区域（x>800, y<200）
                if x > 800 and y < 200:
                    self.tap(x, y, "关闭启动弹窗")
                    miss_count = 0  # 重置未匹配计数
                    print("  ⏳ 等待5秒...")
                    self.time.sleep(5)
                else:
                    miss_count += 1
                    print(f"  ⚠️ 位置不匹配 ({x},{y})，未匹配 {miss_count}/3")
            else:
                miss_count += 1
                print(f"  ⚠️ 未找到弹窗，未匹配 {miss_count}/3")

            # 连续3次未匹配，认为弹窗全部关闭
            if miss_count >= 3:
                print("  ✅ 启动弹窗处理完毕（连续3次未匹配）")
                break

        return True

    def enter_game(self):
        """进入游戏: 点击开始游戏"""
        print("\n[FarmWatch / 进入游戏] 点击开始游戏...")

        for attempt in range(5):
            print(f"  尝试 {attempt+1}/5...")
            self.screenshot(self.SCREENSHOT_PATH)

            start_match = self.locate_start_control(self.SCREENSHOT_PATH)
            if start_match is not None:
                self.tap(start_match["x"], start_match["y"], "开始游戏")
                print("  ⏳ 等待40秒...")
                self.time.sleep(40)
                return True

            if attempt < 4:
                print("  ⏳ 等待5秒...")
                self.time.sleep(5)

        print("  ❌ 连续5次失败，返回设备状态")
        return False

    def ensure_account_ready(self,
        *,
        timeout_seconds=None,
        poll_interval=None,
        transition_timeout=None,
        max_state_attempts=None,
        screenshot_fn=None,
        detect_fn=None,
        tap_fn=None,
        sleep_fn=None,
        monotonic_fn=None,
        signature_fn=None,
        diagnostic_fn=None,
    ) -> bool:
        """处理掉线后的隐私、QQ 登录和两次授权页面，直到开始游戏页出现。"""
        if timeout_seconds is None:
            timeout_seconds = self.LOGIN_FLOW_TIMEOUT
        if poll_interval is None:
            poll_interval = self.LOGIN_POLL_INTERVAL
        if transition_timeout is None:
            transition_timeout = self.LOGIN_TRANSITION_TIMEOUT
        if max_state_attempts is None:
            max_state_attempts = self.LOGIN_MAX_STATE_ATTEMPTS
        screenshot_fn = self.screenshot if screenshot_fn is None else screenshot_fn
        tap_fn = self.tap if tap_fn is None else tap_fn
        sleep_fn = self.time.sleep if sleep_fn is None else sleep_fn
        monotonic_fn = self.time.monotonic if monotonic_fn is None else monotonic_fn
        signature_fn = self._screen_signature if signature_fn is None else signature_fn
        using_default_diagnostic = diagnostic_fn is None
        diagnostic_fn = self._save_login_diagnostic if diagnostic_fn is None else diagnostic_fn

        if detect_fn is None:
            def detect_fn(template_name, screenshot_path):
                if template_name == "session.start":
                    return self.locate_start_control(screenshot_path)
                return self.locate_login_control(template_name, screenshot_path)

        state = "CHECK"
        failed_attempts = {}
        unknown_count = 0
        qq_agree_transition_reference = None

        def fail_with_diagnostic(error=None, screenshot_is_stale=False):
            try:
                if using_default_diagnostic:
                    diagnostic_fn(
                        state,
                        self.SCREENSHOT_PATH,
                        error=error,
                        screenshot_is_stale=screenshot_is_stale,
                    )
                else:
                    # 保留公开注入回调的既有二参数接口。
                    diagnostic_fn(state, self.SCREENSHOT_PATH)
            except Exception as exc:
                print(f"  [WARN] login diagnostic callback failed: {exc}")
            return False

        try:
            deadline = monotonic_fn() + timeout_seconds

            while monotonic_fn() < deadline:
                screenshot_fn(self.SCREENSHOT_PATH)

                # 开始游戏页在所有状态下都有最高优先级。
                if detect_fn("session.start", self.SCREENSHOT_PATH) is not None:
                    return True

                action = None
                if state == "CHECK":
                    match = detect_fn("account.privacy", self.SCREENSHOT_PATH)
                    if match is not None:
                        action = (
                            match,
                            "隐私协议同意",
                            "QQ_LOGIN",
                            "account.android",
                            False,
                        )
                    else:
                        match = detect_fn("account.android", self.SCREENSHOT_PATH)
                        if match is not None:
                            # 隐私页已跳过时，当前动作本身就是 QQ_LOGIN。
                            # 这样超时后的迟到授权页仍可由该状态安全吸收。
                            state = "QQ_LOGIN"
                            action = (
                                match,
                                "QQ 安卓登录",
                                "QQ_AGREE_1",
                                "account.authorize",
                                False,
                            )
                elif state == "QQ_LOGIN":
                    # 上一次 QQ 登录点击可能刚过 transition_timeout 才完成。
                    # 看到授权页即可安全吸收该迟到语义转移，不能当 unknown。
                    late_target = detect_fn("account.authorize", self.SCREENSHOT_PATH)
                    if late_target is not None:
                        state = "QQ_AGREE_1"
                        failed_attempts.pop("QQ_LOGIN", None)
                        unknown_count = 0
                        continue
                    else:
                        match = detect_fn("account.android", self.SCREENSHOT_PATH)
                        if match is not None:
                            action = (
                                match,
                                "QQ 安卓登录",
                                "QQ_AGREE_1",
                                "account.authorize",
                                False,
                            )
                elif state == "QQ_AGREE_1":
                    match = detect_fn("account.authorize", self.SCREENSHOT_PATH)
                    if match is not None:
                        if (
                            qq_agree_transition_reference is not None
                            and self._login_match_center_moved(
                                qq_agree_transition_reference, match
                            )
                        ):
                            # 第一次授权点击已迟到完成；当前位置已经是第二段授权。
                            state = "QQ_AGREE_2"
                            qq_agree_transition_reference = None
                            failed_attempts.pop("QQ_AGREE_1", None)
                            unknown_count = 0
                            continue
                        else:
                            action = (
                                match,
                                "QQ 授权同意",
                                "QQ_AGREE_2",
                                "account.authorize",
                                True,
                            )
                elif state == "QQ_AGREE_2":
                    match = detect_fn("account.authorize", self.SCREENSHOT_PATH)
                    if match is not None:
                        # 第二次授权只有 session.start 才是成功证据。
                        action = (match, "QQ 授权同意", None, None, False)

                if action is None:
                    unknown_count += 1
                    if unknown_count >= self.LOGIN_UNKNOWN_LIMIT:
                        return fail_with_diagnostic()
                    remaining = deadline - monotonic_fn()
                    if remaining <= 0:
                        break
                    sleep_fn(min(poll_interval, remaining))
                    continue

                unknown_count = 0
                match, label, next_state, target_template, require_center_shift = action
                before_signature = signature_fn(self.SCREENSHOT_PATH)
                if state == "QQ_AGREE_1":
                    qq_agree_transition_reference = {
                        "x": match["x"],
                        "y": match["y"],
                    }
                tap_fn(match["x"], match["y"], label)

                transition_deadline = min(
                    deadline, monotonic_fn() + transition_timeout
                )
                transitioned = False
                saw_visual_change = False
                while True:
                    screenshot_fn(self.SCREENSHOT_PATH)
                    if detect_fn("session.start", self.SCREENSHOT_PATH) is not None:
                        return True

                    after_signature = signature_fn(self.SCREENSHOT_PATH)
                    if (
                        before_signature is not None
                        and after_signature is not None
                        and after_signature != before_signature
                    ):
                        saw_visual_change = True

                    target_match = None
                    if target_template is not None:
                        target_match = detect_fn(target_template, self.SCREENSHOT_PATH)
                    if target_match is not None and (
                        not require_center_shift
                        or self._login_match_center_moved(match, target_match)
                    ):
                        state = next_state
                        if state == "QQ_AGREE_2":
                            qq_agree_transition_reference = None
                        transitioned = True
                        break

                    remaining = transition_deadline - monotonic_fn()
                    if remaining <= 0:
                        break
                    sleep_fn(min(poll_interval, remaining))

                if transitioned:
                    continue

                if saw_visual_change:
                    print(
                        f"  [WARN] login state {state} image changed "
                        "without the required semantic transition"
                    )

                failed_attempts[state] = failed_attempts.get(state, 0) + 1
                if failed_attempts[state] >= max_state_attempts:
                    return fail_with_diagnostic()

            return fail_with_diagnostic()
        except ScreenshotCaptureError as exc:
            print(f"  [WARN] login flow failed in state {state}: {exc}")
            return fail_with_diagnostic(error=exc, screenshot_is_stale=True)
        except Exception as exc:
            print(f"  [WARN] login flow failed in state {state}: {exc}")
            return fail_with_diagnostic(error=exc)

    def dismiss_lobby_overlays(self):
        """大厅弹窗: 关闭弹窗（多个弹窗依次关闭）"""
        print("\n[FarmWatch / 大厅弹窗] 关闭弹窗...")

        miss_count = 0
        for i in range(10):  # 最多处理10个弹窗
            self.screenshot(self.SCREENSHOT_PATH)
            result = self.locate_control("dialog.close", self.SCREENSHOT_PATH, 0.88)

            if result:
                x, y = result["x"], result["y"]
                # 位置校验：弹窗关闭按钮应在屏幕中央区域（x>800, y<200）
                if x > 800 and y < 200:
                    self.tap(x, y, "关闭弹窗")
                    miss_count = 0  # 重置未匹配计数
                    print("  ⏳ 等待5秒...")
                    self.time.sleep(5)
                else:
                    miss_count += 1
                    print(f"  ⚠️ 位置不匹配 ({x},{y})，未匹配 {miss_count}/3")
            else:
                miss_count += 1
                print(f"  ⚠️ 未找到弹窗，未匹配 {miss_count}/3")

            # 连续3次未匹配，认为弹窗全部关闭
            if miss_count >= 3:
                print("  ✅ 弹窗处理完毕（连续3次未匹配）")
                break

        return True

    def wait_farm_scene_stable(self,
        timeout_s=15.0, interval_s=1.5, diff_threshold=3.0
    ):
        """等待农场画面稳定后再走位。

        冷启动刚进农场时场景可能仍在加载/转场，首次摇杆滑动可能少走甚至
        没走（表现为一键务农按钮位置异常，或执行务农失败后重试才正常）。这里
        连续比对降采样灰度帧，画面变化足够小即认为稳定；检测自身异常时
        不阻塞主流程。
        """
        try:
            start = self.time.time()
            prev = None
            while self.time.time() - start < timeout_s:
                self.screenshot(self.SCREENSHOT_PATH, archive_label="scene_stable")
                img = cv2.imread(self.SCREENSHOT_PATH, cv2.IMREAD_GRAYSCALE)
                if img is None:
                    print("  ⚠️ 无法读取画面帧，跳过稳定检测")
                    return True
                small = cv2.resize(img, (160, 90))
                if prev is not None:
                    diff = float(np.mean(cv2.absdiff(small, prev)))
                    if diff < diff_threshold:
                        print("  ✅ 农场画面已稳定")
                        return True
                prev = small
                self.time.sleep(interval_s)
            print("  ⚠️ 等待画面稳定超时，按现状继续")
            return False
        except Exception as e:
            print(f"  ⚠️ 画面稳定检测异常({e})，按现状继续")
            return True

    def open_farm(self):
        """进入农场: 进入农场"""
        print("\n[FarmWatch / 进入农场] 进入农场...")

        for attempt in range(10):  # 最多尝试10次
            self.screenshot(self.SCREENSHOT_PATH)

            if self.press_first_control(
                ("farm.enter",),
                self.SCREENSHOT_PATH,
                0.55,
                "进入农场",
                (0.05, 0.60, 0.50, 0.90),
            ):
                print("  ⏳ 等待40秒...")
                self.time.sleep(40)
                self.wait_farm_scene_stable()
                try:
                    self.screenshot(self.SCREENSHOT_PATH, archive_label="farm_entry")
                    print("  ✅ 已确认进入农场后的画面")
                except ScreenshotCaptureError as exc:
                    print(f"  ⚠️ 进入农场后的截图失败: {exc}")
                    if self._is_adb_transport_failure(exc):
                        return False
                return True

            print(f"  ⏳ 等待5秒... ({attempt+1}/10)")
            self.time.sleep(5)

        print("  ❌ 连续10次未找到进入农场按钮")
        return False

    def approach_farm_control(self, max_attempts=3):
        """固定路线、小步确认；找不到按钮时复原后重试，不累计加长走位。"""
        print("\n[FarmWatch / 务农定位] 移动到雕像...")
        self.screenshot(self.SCREENSHOT_PATH)
        if self.locate_farm_action(self.SCREENSHOT_PATH) is not None:
            print("  ✅ 已在一键务农处，无需再移动")
            return True

        cfg = self.statue_path
        nudge_ms = 200
        max_nudges = 3
        for attempt in range(1, max_attempts + 1):
            # 复原图标在角色走开后仍然可见，不能用它推断角色在起点。
            # 每条完整路线都先明确复原；复原失败时禁止从未知位置盲走。
            print(f"  🔄 复原到起点后重试 ({attempt}/{max_attempts})")
            if not self.reset_position():
                print("  ❌ 无法确认复原操作，停止本次定位")
                break

            print(f"  🚶 从起点移动 {cfg['duration']}ms")
            self.move_joystick(
                cfg["angle"], cfg["distance"], cfg["duration"], center=cfg["center"]
            )
            self.time.sleep(5)

            for nudge in range(max_nudges + 1):
                self.screenshot(self.SCREENSHOT_PATH)
                if self.locate_farm_action(self.SCREENSHOT_PATH) is not None:
                    print("  ✅ 一键务农按钮已出现")
                    return True
                if nudge == max_nudges:
                    break

                print(f"  🚶 小步补走 ({nudge + 1}/{max_nudges}, {nudge_ms}ms)")
                self.move_joystick(
                    cfg["angle"], cfg["distance"], nudge_ms, center=cfg["center"]
                )
                self.time.sleep(1)

            if attempt < max_attempts:
                print("  ⚠️ 三次小步后仍未找到按钮，复原后重新走固定路线")

        self.screenshot(self.SCREENSHOT_PATH, archive_label="approach_fail")
        print("  ❌ 定位重试后仍未找到一键务农按钮")
        return False

    def perform_farm_action(self):
        """执行务农: 用点击前的新截图定位；ADB 拒绝/超时不算成功。"""
        print("\n[FarmWatch / 执行务农] 一键务农...")
        self.screenshot(self.SCREENSHOT_PATH)
        if self.locate_farm_action(self.SCREENSHOT_PATH) is None:
            print("  ❌ 未找到一键务农，返回务农定位")
            return False

        print("  ✅ 找到一键务农按钮")
        print("  ⏳ 等待3秒...")
        self.time.sleep(3)
        self.screenshot(self.SCREENSHOT_PATH)
        match = self.locate_farm_action(self.SCREENSHOT_PATH)
        if match is None:
            print("  ❌ 点击前按钮已消失，返回务农定位重新定位")
            return False

        x, y = match["x"], match["y"]
        print(f"  👆 tap ({x}, {y}) [一键务农]")
        try:
            clicked = self.adb_shell_result(f"input tap {x} {y}")
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  ❌ 一键务农点击异常: {exc}")
            return False
        if clicked.returncode != 0:
            print(f"  ❌ 一键务农点击失败: rc={clicked.returncode}; "
                  f"stdout={clicked.stdout!r}; stderr={clicked.stderr!r}")
            return False
        print("  ⏳ ADB 已接受点击，等待5秒；游戏内结果由后续步骤读取")
        self.time.sleep(5)
        return True

    def collect_harvest_result(self):
        """收获结果: 关闭收获弹窗，返回 (success, harvested)"""
        print("\n[FarmWatch / 收获结果] 关闭收获弹窗...")
        harvested = False

        for attempt in range(3):
            self.screenshot(self.SCREENSHOT_PATH)

            if self.control_visible("harvest.continue", self.SCREENSHOT_PATH, 0.8):
                self.archive_current('harvest')
                print("  ✅ 找到收获弹窗")
                harvested = True

                # OCR识别收获信息
                harvest_info = self.read_harvest_info(self.SCREENSHOT_PATH)
                if harvest_info:
                    exp = harvest_info["exp"]
                    crops = harvest_info["crops"]
                    detail = []
                    if exp > 0:
                        detail.append(f"经验+{exp}")
                    for cname, ccount in crops.items():
                        detail.append(f"{cname}×{ccount}")
                    print(f"  🎉 收获: {' '.join(detail)}")
                    self.stats.last_detail = " ".join(detail)
                    self.stats.add_harvest(exp=exp, crops=crops)
                else:
                    self.stats.last_detail = ""
                    self.stats.add_harvest()

                print("  ⏳ 等待3秒...")
                self.time.sleep(3)

                self.press_control("harvest.continue", self.SCREENSHOT_PATH, 0.8, "继续")
                print("  ⏳ 等待5秒...")
                self.time.sleep(5)
                return True, harvested
            else:
                print(f"  ⚠️ 未找到收获弹窗，等待3秒后重试 ({attempt+1}/3)")
                self.time.sleep(3)

        print("  ⚠️ 连续3次未找到收获弹窗，进入作物识别")
        return False, False

    def inspect_crop(self,
        first_water_time,
        save_if_fresh=False,
        return_status=False,
        cycle_hint=None,
        crop_name=None,
    ):
        """作物识别: 移动到土地，读取成熟时间，计算浇水计划

        :param first_water_time: 一键务农时间（由main传入，在移动前记录）
        :param save_if_fresh: 如果是新种植（刚收获），保存计算的周期
        :param return_status: 是否额外返回 OCR 的成熟状态
        :param cycle_hint: 当前这批作物已经确认的周期
        :param crop_name: 可选的作物名称提示；通常由本步骤 OCR 自动读取
        """
        print("\n[FarmWatch / 作物识别] 移动到土地...")

        # 向上方移动
        self.move_joystick(90, 200, 1200)  # 90度是正上方
        print("  ⏳ 等待移动...")
        self.time.sleep(5)

        # 截图 OCR。新的读取器会在同一次 OCR 中提取作物名称和成熟时间；
        # 对旧调用/旧截图保留 read_maturity_time() 回退，避免名称区域改版
        # 或 OCR 临时失败时把“无法识别”误判成“已成熟”。
        self.screenshot(self.SCREENSHOT_PATH)
        state = None
        try:
            state = self.read_crop_state(
                self.SCREENSHOT_PATH, reference_time=first_water_time
            )
        except Exception as exc:
            # OCR 失败不应阻断后面的兼容读取路径。
            print(f"  ⚠️ 作物状态 OCR 失败，回退成熟时间 OCR: {exc}")

        if not isinstance(state, dict):
            state = {}

        detected_name = self.normalize_crop_name(state.get("crop_name"))
        if detected_name is None:
            detected_name = self.normalize_crop_name(crop_name)

        maturity_time = state.get("maturity_time")
        is_mature = bool(state.get("is_mature"))

        # 只接受完整的 (hour, minute, day_offset) 元组。状态 OCR 可能只
        # 读到了作物名称，这时必须使用旧的成熟时间 ROI 再尝试一次。
        valid_maturity = (
            isinstance(maturity_time, (tuple, list))
            and len(maturity_time) == 3
        )
        if not is_mature and not valid_maturity:
            try:
                fallback_time, fallback_mature = self.read_maturity_time(
                    self.SCREENSHOT_PATH, reference_time=first_water_time
                )
            except Exception as exc:
                print(f"  ⚠️ 成熟时间 OCR 也失败: {exc}")
                fallback_time, fallback_mature = None, False
            maturity_time = fallback_time
            is_mature = bool(fallback_mature)

        # 如果作物已成熟（可收获），不需要等待
        if is_mature:
            print("  🌾 作物已成熟，无需等待")
            result = (None, None, None)
            return (*result, True) if return_status else result

        # 计算浇水计划
        result = None
        maturity_dt = None

        if maturity_time:
            maturity_dt = self.resolve_maturity_datetime(first_water_time, maturity_time)

            # 调用新的浇水计算函数
            calculate_kwargs = {"save_if_fresh": save_if_fresh}
            if cycle_hint is not None:
                calculate_kwargs["cycle_hint"] = cycle_hint
            if detected_name is not None:
                calculate_kwargs["crop_name"] = detected_name
            result = self.calculate_plant_cycle_and_water_time(
                first_water_time,
                maturity_dt,
                **calculate_kwargs,
            )

        result_tuple = (maturity_time, result, maturity_dt)
        return (*result_tuple, False) if return_status else result_tuple

    def _exit_game_for_wait(self):
        print("  🛑 退出王者荣耀...")
        self.adb_shell(f"am force-stop {self.GAME_PKG}")

    def visit_pinned_friends(self, wake_time):
        """好友拿取使用既定的绝对唤醒时间，不延长自家务农计划。"""
        print("\n[FarmWatch / 好友访问] 检查可拿取的置顶好友...")
        return friends.run(self, wake_time)

    def schedule_next_round(self, maturity_time, result, maturity_dt, is_mature=None, *, exit_game=True):
        """计划等待: 根据成熟时间和浇水时间计算等待时间

        :param maturity_time: OCR识别的 (hour, minute, day_offset) 元组
        :param result: 浇水计算结果 dict
        :param maturity_dt: 成熟时间 (datetime)
        :param is_mature: OCR 是否明确识别到可收获状态；None 保持旧调用兼容
        :return: wake_time (下次唤醒时间)

        同时把真实目标时刻写入 ``_pending_target_time``，供 ``wait_until_target_time``
        在点击一键务农前补齐提前唤醒量。
        """

        print("\n[FarmWatch / 计划等待] 计算等待时间...")

        # 每轮重新计算，先清掉上一轮的目标，避免识别失败时沿用过期时刻。
        self._pending_target_time = None
        self._pending_target_is_absolute = False

        no_schedule = maturity_time is None and result is None and maturity_dt is None

        # 新调用明确传入 is_mature；旧调用没有状态时保留原有全 None 行为。
        if is_mature is True or (is_mature is None and no_schedule):
            print("  🌾 作物已成熟，退出游戏重新进入收割...")
            self.adb_shell(f"am force-stop {self.GAME_PKG}")

            self.time.sleep(3)
            return None

        # 好友拿取开启时先保留游戏，结束后由 main 的 finally 统一退出。
        if exit_game:
            self._exit_game_for_wait()

        now = self.datetime.now()

        # OCR 没有读到成熟时间时，不要把它误判为已成熟而立即重启；
        # 退出游戏后延迟重试，避免识别失败造成忙循环。
        if is_mature is False and no_schedule:
            print("  ⚠️ 无法识别时间，5分钟后重试...")
            return now + timedelta(minutes=5)

        # 确定唤醒时间。规划器返回的 next_action/next_watering 是唯一的
        # 新接口来源；water2 仅作为旧版本调用方的兼容回退。成熟目标也
        # 优先使用规划器计算出的 mature_time，因为浇水可能使它早于 OCR
        # 初始显示的 maturity_dt。
        wake_time = None
        reason = ""
        plan = result if isinstance(result, dict) else None
        planned_watering = None
        planned_maturity = None

        def _valid_datetime(value):
            return isinstance(value, self._DATETIME_TYPE)

        if plan:
            action = plan.get("next_action")
            planned_watering = plan.get("next_watering")
            planned_maturity = plan.get("mature_time")
            legacy_watering = plan.get("water2")

            if action == "water" and _valid_datetime(planned_watering):
                wake_time = planned_watering
                reason = "浇水"
                print(
                    f"  💧 下次浇水时间: {planned_watering.strftime('%H:%M:%S')}"
                )
            elif action == "water" and _valid_datetime(legacy_watering):
                # A partially migrated result may expose next_action but only the
                # legacy absolute watering field.
                wake_time = legacy_watering
                reason = "浇水"
                print(
                    f"  💧 下次浇水时间: {legacy_watering.strftime('%H:%M:%S')}"
                )
            elif action == "mature" and _valid_datetime(planned_maturity):
                wake_time = planned_maturity
                reason = "成熟"
                print(
                    f"  🌾 规划成熟时间: {planned_maturity.strftime('%H:%M:%S')}"
                )
            elif _valid_datetime(planned_maturity):
                # 兼容只返回 mature_time 的外部规划结果。
                wake_time = planned_maturity
                reason = "成熟"
                print(
                    f"  🌾 规划成熟时间: {planned_maturity.strftime('%H:%M:%S')}"
                )
            elif _valid_datetime(planned_watering):
                # 兼容缺少 next_action 的旧结果，但仍优先执行下次浇水。
                wake_time = planned_watering
                reason = "浇水"
                print(
                    f"  💧 下次浇水时间: {planned_watering.strftime('%H:%M:%S')}"
                )
            else:
                # 最后回退到旧 water2/water3 字段。
                if _valid_datetime(legacy_watering) and (
                    not _valid_datetime(maturity_dt) or legacy_watering < maturity_dt
                ):
                    wake_time = legacy_watering
                    reason = "浇水"
                    print(
                        f"  💧 下次浇水时间: {legacy_watering.strftime('%H:%M:%S')}"
                    )

        if wake_time is None and _valid_datetime(maturity_dt):
            wake_time = maturity_dt
            reason = "成熟"
            print(f"  🌾 成熟时间: {maturity_dt.strftime('%H:%M:%S')}")

        if wake_time is None:
            print("  ⚠️ 无法识别时间，5分钟后重试...")
            return now + timedelta(minutes=5)

        # 按配置提前唤醒（默认提前5分钟）。收菜目标再加保护延迟，
        # 避免比游戏成熟时间早几秒点一键务农，把收获误变成浇水。
        # 最后一次浇水正好等于成熟时间时，也视为收菜保护目标：这时早几秒
        # 会让一键务农执行成浇水，而不是收割。
        final_harvest_watering = (
            reason == "浇水"
            and _valid_datetime(planned_watering)
            and _valid_datetime(planned_maturity)
            and abs((planned_watering - planned_maturity).total_seconds()) <= 1
        )
        harvest_safe_target = reason == "成熟" or final_harvest_watering
        target_time = wake_time
        if harvest_safe_target and self.HARVEST_DELAY_SEC:
            target_time += timedelta(seconds=self.HARVEST_DELAY_SEC)
        self._pending_target_time = target_time
        wake_time = target_time - timedelta(minutes=self.WAKE_AHEAD_MIN)

        if wake_time <= now:
            print(f"  ⚠️ {reason}时间已到，立即重新启动")
            return now

        wait_seconds = int((wake_time - now).total_seconds())
        hours = wait_seconds // 3600
        minutes = (wait_seconds % 3600) // 60
        seconds = wait_seconds % 60

        delay_text = (
            f"，含{self.HARVEST_DELAY_SEC}秒收菜延迟"
            if harvest_safe_target else ""
        )
        print(
            f"  🎯 目标时间: {target_time.strftime('%H:%M:%S')} "
            f"({reason}{delay_text})"
        )
        print(f"  🔔 提前{self.WAKE_AHEAD_MIN}分钟唤醒: {wake_time.strftime('%H:%M:%S')}")
        print(f"  ⏳ 等待 {hours}小时{minutes}分{seconds:02d}秒")

        return wake_time

    def wait_until_target_time(self):
        """点击一键务农前补齐提前唤醒量，避免早于目标时刻操作。

        ``schedule_next_round`` 会提前 ``WAKE_AHEAD_MIN`` 分钟唤醒，好让模拟器
        与游戏有时间启动。若启动比预留时间快，就会早于浇水/成熟时刻点到一键
        务农，该次操作不会生效，白跑一轮。这里把剩余时间补齐后再返回。

        目标时刻消费一次即清空：启动途中失败重试的轮次尚未走到这里，目标会
        保留到真正点击前生效。

        :return: 是否实际等待过
        """

        target_time = self._pending_target_time
        absolute_target = self._pending_target_is_absolute
        self._pending_target_time = None
        self._pending_target_is_absolute = False
        if not isinstance(target_time, self._DATETIME_TYPE):
            return False

        remaining = (target_time - self.datetime.now()).total_seconds()
        if remaining <= 0:
            return False

        # 正常情况下 remaining 不会超过提前量；夹紧以防未来改动破坏该前提，
        # 避免误读的目标时间把脚本挂在游戏里空等。
        if not absolute_target:
            remaining = min(remaining, self.WAKE_AHEAD_MIN * 60)
        print(
            f"  ⏸️ 早于目标时间 {target_time.strftime('%H:%M:%S')}，"
            f"补等 {int(remaining)} 秒后再务农"
        )
        self.time.sleep(remaining)
        return True

    def wait_for_next_round(self, wake_time) -> bool:
        """等待下一轮唤醒时间，并在正常等待期间重启模拟器。

        已到达（或超过）唤醒时间时不关闭/重启模拟器，直接交给调用方继续
        唤醒解锁。正常等待时先关闭模拟器，等待主机时间到达后再启动；关闭
        失败不阻断等待，启动失败则返回 ``False`` 让主流程安全停止。
        """
        now = self.datetime.now()
        if wake_time <= now:
            print("  ⏱️ 唤醒时间已到，跳过模拟器关闭与重启")
            return True

        print(f"\n⏳ 等待到 {wake_time.strftime('%H:%M:%S')} 唤醒...")
        # 关闭失败由 shutdown_emulator_for_wait 自身记录诊断；等待仍继续。
        self.shutdown_emulator_for_wait()

        remaining_seconds = (wake_time - self.datetime.now()).total_seconds()
        if remaining_seconds > 0:
            target = self._pending_target_time
            self.status_callback(
                'waiting', wake_time=wake_time.isoformat(),
                target_time=target.isoformat() if isinstance(target, self._DATETIME_TYPE) else None,
            )
            self.time.sleep(remaining_seconds)

        if not self.ensure_emulator_running():
            print("  ❌ 等待结束后 MuMu 启动失败，停止后续流程")
            return False

        # 显式指定 FARMWATCH_DEVICE 时，ensure_emulator_running 为了发现 MuMu
        # 动态端点仍会查询实例，但会刻意保留用户指定的 DEVICE。重启后再
        # 校验该显式端点，避免后续 ADB 操作继续使用已断开的旧地址。默认
        # 动态地址路径已经在 MuMu 控制层完成 ADB 就绪检查，无需重复连接。
        if self._manage_mumu and self._device_from_env and not self.check_adb_connection():
            print("  ❌ 显式 FARMWATCH_DEVICE 在模拟器重启后未连接，停止后续流程")
            return False
        self.status_callback('running', device=self.DEVICE)
        return True

    def main(self):
        """主流程"""
        print("=" * 60)
        print("王者农场助手 · FarmWatch")
        print("=" * 60)
        print("  画布：1280×720 · MuMu / Android")

        # 重启等待中的后台时先恢复等待，不提前启动游戏或发送务农动作。
        resume_wake = self._resume_wake_time
        self._resume_wake_time = None
        if isinstance(resume_wake, self._DATETIME_TYPE):
            print(f"  🔄 恢复原唤醒计划: {resume_wake:%Y-%m-%d %H:%M:%S}")
            if not self.wait_for_next_round(resume_wake):
                return 1

        if self._manage_mumu:
            print(f"\n🖥️ 准备 MuMu 实例: {self.MUMU_NAME}")
        elif self._device_from_env:
            print(f"\n🔌 使用外部 ADB 设备: {self.DEVICE}")
        else:
            reason = self._mumu_management_disabled_reason()
            print(f"\n🔌 {reason}；使用 ADB 设备: {self.DEVICE}")
        if not self.ensure_emulator_running():
            self.notify_error_exit("无法启动或复用 MuMu 模拟器实例")
            return 1

        # 启动前检查ADB连接
        if not self.check_adb_connection():
            self.notify_error_exit("ADB 连接检查失败，脚本启动后立即退出")
            return 1

        # 检测设备分辨率
        dev_w, dev_h = self.detect_resolution()
        print(f"  📐 分辨率 {dev_w}x{dev_h}")
        if (dev_w, dev_h) != (1280, 720):
            raise RuntimeError("仅支持 1280×720，请调整 MuMu 分辨率后重新启动")
        self.status_callback('running', device=self.DEVICE)

        self.JOYSTICK_CENTER = self.statue_path['center']

        round_num = 0
        current_cycle_hint = None
        pending_fresh_cycle = False
        action_failures = 0
        while True:
            round_num += 1
            self.stats.rounds = round_num
            print(f"\n{'='*60}")
            print(f"# 第 {round_num} 轮务农")
            print(f"{'='*60}")

            # 设备状态: 检测状态
            self.report_device_state()

            # 启动游戏: 启动游戏
            if not self.launch_game():
                print("\n⚠️ 游戏启动失败，跳过本轮后续操作并重试……")
                self.time.sleep(5)
                continue

            # 启动游戏a: 系统级检测游戏资源更新；更新完成前不点开始游戏。
            if not self.wait_for_game_update():
                self.notify_error_exit("等待游戏资源更新超时")
                return 1

            # 启动弹窗: 关闭启动弹窗
            self.dismiss_startup_overlays()

            # 账号检查: 确认登录状态后再进入游戏
            if not self.ensure_account_ready():
                print("\n⚠️ 登录检查失败，重新开始……")
                continue

            # 进入游戏: 点击开始游戏
            if not self.enter_game():
                print("\n⚠️ 进入游戏失败，重新开始...")
                continue

            # 大厅弹窗: 关闭弹窗
            self.dismiss_lobby_overlays()

            # 进入农场: 进入农场
            if not self.open_farm():
                print("\n⚠️ 进入农场失败，重新开始...")
                continue

            # 只有定位成功才等待并点击；定位内部已经执行有界复原重试。
            positioned = self.approach_farm_control()
            farmed = False
            if positioned:
                # 补齐提前唤醒量，确保不早于目标时刻点击一键务农。
                self.wait_until_target_time()
                farmed = self.perform_farm_action()
                if not farmed:
                    print("\n⚠️ 执行务农失败，返回务农定位...")
                    try:
                        self.screenshot(self.SCREENSHOT_PATH, archive_label="action_fail")
                    except Exception:
                        pass
                    if self.approach_farm_control():
                        farmed = self.perform_farm_action()
            if not farmed:
                self.last_round = RoundResult(RoundStatus.ACTION_FAILED)
                action_failures += 1
                # 定位阶段失败时保留尚未消费的目标，重试也不能提前务农。
                self._exit_game_for_wait()
                if action_failures >= 3:
                    raise RuntimeError("一键务农连续三轮失败，已停止，未生成成功调度")
                print("  ⚠️ 本轮定位或点击失败，30 秒后重新进入农场恢复")
                self.time.sleep(30)
                continue
            action_failures = 0
            self.last_round = RoundResult(RoundStatus.CLICK_SENT, click_sent=True)

            # 收获结果: 关闭收获弹窗
            _, harvested = self.collect_harvest_result()

            # 收获后已经进入新一批作物，不能复用上一批作物的周期。
            if harvested:
                current_cycle_hint = None
                pending_fresh_cycle = True
                self.clear_crop_cycle()

            # 记录一键务农时间（在作物识别移动前）
            first_water_time = self.datetime.now()
            print(f"  🕐 一键务农时间: {first_water_time.strftime('%H:%M:%S')}")

            # 作物识别: 移动到土地，读取成熟时间，计算浇水计划
            inspection_options = {
                "save_if_fresh": pending_fresh_cycle and bool(farmed),
                "return_status": True,
            }
            if current_cycle_hint is not None:
                inspection_options["cycle_hint"] = current_cycle_hint

            maturity_time, result, maturity_dt, is_mature = self.inspect_crop(
                first_water_time, **inspection_options
            )
            self.last_round = RoundResult(
                RoundStatus.HARVEST_CONFIRMED if harvested else
                RoundStatus.CLICK_SENT if result is not None or is_mature else RoundStatus.RECOGNITION_FAILED,
                click_sent=True, harvest_confirmed=harvested, plan=result,
            )

            # 首次浇水窗口只能消费一次：即使 OCR 失败，也不能把后续浇水当新样本。
            if farmed:
                pending_fresh_cycle = False
            # OCR 成功仍更新本批次规划提示；失败不丢弃已有提示。
            if result is not None and "plant_cycle_min" in result:
                current_cycle_hint = result["plant_cycle_min"]

            # 先保存自家计划，再访问好友；未知浇水结果不进入偷菜流程。
            steal_enabled = (
                self.settings.friends_enabled
                and (dev_w, dev_h) == (1280, 720)
                and bool(farmed) and result is not None and is_mature is False
            )
            wait_kwargs = {"is_mature": is_mature}
            if steal_enabled:
                wait_kwargs["exit_game"] = False
            wake_time = self.schedule_next_round(
                maturity_time, result, maturity_dt, **wait_kwargs
            )

            if steal_enabled:
                try:
                    if wake_time is not None:
                        self.visit_pinned_friends(wake_time)
                except Exception as exc:
                    print(f"  ⚠️ 好友拿取中止，继续原定等待计划: {exc}")
                finally:
                    self._exit_game_for_wait()

            # 每轮一键务农成功（浇水/收获）都推送通知，正文为本轮浇水计划
            if farmed and (harvested or result is not None):
                self.notify_round(round_num, harvested, plan=result, wake_time=wake_time)
            elif farmed:
                print("  ⚠️ 点击已发送，但未读到收获或作物计划，不推送务农成功")

            if self.max_rounds is not None and round_num >= self.max_rounds:
                print('单轮检查完成')
                return 0

            if wake_time is None:
                # 作物已成熟，立即重新开始
                continue

            # 正常等待时关闭模拟器，等待结束后重新启动；成熟立即重试不走此路径。
            if not self.wait_for_next_round(wake_time):
                return 1

            self.wake_screen()

    def run_main(self):
        """返回可靠退出码，退出时释放本实例管理的模拟器。"""
        exit_code = 0
        try:
            exit_code = self.main() or 0
        except KeyboardInterrupt:
            print("\n\n停止请求已收到")
        except Exception as e:
            logging.getLogger('farmwatch').exception("主流程异常退出")
            self.notify_error_exit(e)
            exit_code = 1
        finally:
            # 脚本结束后不再占用模拟器资源。正常等待路径会先关闭 MuMu；
            # 这里兜底处理异常退出、用户中断和主流程自然结束。
            if self._manage_mumu:
                print("\n🛑 脚本退出，关闭 MuMu...")
                self.shutdown_emulator_for_wait()

            print("\n👋 脚本已退出")
        return exit_code
