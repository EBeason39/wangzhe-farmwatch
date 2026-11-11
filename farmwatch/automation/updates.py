"""王者农场助手：updates。所有可变运行状态归 FarmApp 实例所有。"""

from __future__ import annotations
import re
import json
import xml.etree.ElementTree as ET
import cv2


class GameUpdates:
    def _read_remote_json(self, relative_path):
        """Read and parse a JSON file from the game's ADB-readable data dir."""
        remote_path = f"{self.GAME_UPDATE_DIR}/{relative_path}"
        result = self.adb_shell_result(f"cat '{remote_path}'", timeout=20)
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError):
            return None

    def read_game_update_system_state(self):
        """Return system-level required-resource progress, or None when absent.

        ``new.patchjson.required.pkglist`` is the set that must exist before the
        launcher continues.  ``downloaded_filelist_v2.json`` records downloaded
        package ids.  Matching the required target and required id set is more
        reliable than comparing byte counters, because those counters include
        optional/previously downloaded packages.
        """
        patch = self._read_remote_json("new.patchjson")
        downloaded_doc = self._read_remote_json("downloaded_filelist_v2.json")
        if not isinstance(patch, dict) or not isinstance(downloaded_doc, dict):
            return None

        required = patch.get("required")
        if not isinstance(required, dict):
            return None

        target = str(required.get("ver") or "")
        required_ids = set(required.get("pkglist") or [])
        if not target or not required_ids:
            return None

        done_entry = None
        filelist = downloaded_doc.get("filelist")
        if isinstance(filelist, list):
            for item in filelist:
                if not isinstance(item, dict):
                    continue
                if str(item.get("to_version") or "") != target:
                    continue
                filename = str(item.get("filename") or "")
                # The required stream is named *_rqd_*; the full/optional stream
                # has a different filename and must not make us start early.
                if "_rqd_" in filename:
                    done_entry = item
                    break

        done_ids = set(done_entry.get("pkglist") or []) if done_entry else set()
        finished_count = len(required_ids & done_ids)
        total_count = len(required_ids)
        complete = bool(done_entry) and finished_count >= total_count
        return {
            "updating": not complete,
            "complete": complete,
            "target_version": target,
            "done": finished_count,
            "total": total_count,
            "percent": (finished_count * 100.0 / total_count) if total_count else 0.0,
        }

    def read_game_update_screen_progress(self, screenshot_path=None):
        """OCR fallback for the launcher's bottom resource-update progress bar."""
        path = screenshot_path or self.SCREENSHOT_PATH
        img = cv2.imread(path)
        if img is None:
            return None

        h, w = img.shape[:2]
        roi = img[int(h * 0.84):, :]
        try:
            result, _ = self.get_ocr()(roi)
        except Exception:
            return None

        text = " ".join(line[1] for line in result) if result else ""
        markers = (
            "游戏资源检查",
            "游戏安装包检查",
            "资源检查",
            "安装包检查",
            "下载资源包",
            "正在为您下载资源",
            "正在为您下载安装包资源",
            "预计剩余时间",
        )
        if not any(marker in text for marker in markers):
            return None

        match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%", text)
        percent = float(match.group(1)) if match else None
        return {"updating": True, "percent": percent, "text": text}

    def _read_android_ui_xml(self):
        """Dump and read the current Android accessibility hierarchy."""
        dump_result = self.adb_shell_result(
            "uiautomator dump /sdcard/farmwatch_window_dump.xml", timeout=15
        )
        if dump_result.returncode != 0:
            return None
        read_result = self.adb_shell_result(
            "cat /sdcard/farmwatch_window_dump.xml", timeout=10
        )
        if read_result.returncode != 0 or not read_result.stdout.strip():
            return None
        return read_result.stdout

    def _parse_ui_node_bounds(self, value):
        import re as _re

        match = _re.search(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", str(value))
        if not match:
            return None
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    def handle_android_update_dialog(self):
        """Confirm Android's package-install update dialog when it appears.

        The dialog is rendered by the system rather than the game canvas, so
        accessibility XML is more reliable than OCR.  We require the exact
        question text before looking for the right-hand confirmation button.
        """
        xml_text = self._read_android_ui_xml()
        if not xml_text:
            return False
        normalized_xml = re.sub(r"\s+", "", xml_text)
        if "要更新此应用吗" not in normalized_xml:
            return False

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return False

        candidates = []
        for node in root.iter():
            text = re.sub(r"\s+", "", str(node.attrib.get("text", "")))
            if text not in {"更新", "立即更新", "安装", "继续安装"}:
                continue
            bounds = self._parse_ui_node_bounds(node.attrib.get("bounds"))
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            # System dialogs place buttons in the lower half; rightmost is 确认.
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            if center[1] < 200:
                continue
            candidates.append((center, x2))

        if not candidates:
            return False

        (x, y), _right_edge = max(candidates, key=lambda item: item[1])
        self.tap(int(x), int(y), "确认系统更新")
        return True

    def handle_game_update_prompt(self):
        """Click the launcher's mandatory update confirmation when present.

        The update announcement contains both ``退出`` and ``更新`` buttons.  We
        only click when the prompt text is present, keep the button ROI away from
        announcement prose, and prefer the right-hand exact ``更新`` button.
        """
        self.screenshot(self.SCREENSHOT_PATH)
        img = cv2.imread(self.SCREENSHOT_PATH)
        if img is None:
            return False

        try:
            result, _ = self.get_ocr()(img)
        except Exception:
            return False
        if not result:
            return False

        lines = [(line[1], line[0]) for line in result]
        prompt = any(
            "全新版本已发布" in text or "请更新后进入游戏" in text
            for text, _ in lines
        )
        if not prompt:
            return False

        h, w = img.shape[:2]
        candidates = []
        for text, box in lines:
            normalized = re.sub(r"\s+", "", str(text))
            if normalized not in {"更新", "立即更新"}:
                continue
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            # The confirmation button is in the dialog body, not the bottom
            # resource progress bar or the announcement headline.
            if 0.35 <= cx / w <= 0.90 and 0.45 <= cy / h <= 0.95:
                candidates.append((cx, cy))

        if not candidates:
            return False

        # The right-hand candidate is 更新; 退出 is to its left.
        x, y = max(candidates, key=lambda item: item[0])
        self.tap(int(x), int(y), "确认更新")
        return True

    def wait_for_game_update(self,
        timeout_minutes=None,
        poll_interval=None,
        sleep_func=None,
        monotonic_func=None,
    ):
        """Wait until the launcher's required resource update has finished."""
        if not self.settings.update_enabled:
            return True
        timeout_minutes = self.GAME_UPDATE_TIMEOUT_MIN if timeout_minutes is None else timeout_minutes
        poll_interval = self.GAME_UPDATE_POLL_SEC if poll_interval is None else poll_interval
        sleep_func = sleep_func or self.time.sleep
        monotonic_func = monotonic_func or self.time.monotonic
        deadline = monotonic_func() + timeout_minutes * 60
        clean_reads = 0
        system_seen_updating = False
        clean_reads_needed = 2
        last_report = ""

        print("\n[FarmWatch / 资源更新] 检测游戏资源更新...")
        while True:
            remaining = deadline - monotonic_func()
            if remaining <= 0:
                print("  ❌ 等待游戏资源更新超时")
                return False

            if self.handle_android_update_dialog():
                print("  👆 已确认 Android 系统更新")
                sleep_func(min(5, max(0.1, deadline - monotonic_func())))
                continue

            state = self.read_game_update_system_state()

            # Screen evidence wins during a second update phase (for example the
            # APK/package phase after resource diffs), because the resource JSON
            # can remain from the previous phase.
            screen_progress = self.read_game_update_screen_progress()
            if screen_progress is not None:
                clean_reads = 0
                system_seen_updating = True
                percent = screen_progress.get("percent")
                report = (
                    f"  ⏳ 屏幕更新状态: {percent:.1f}%"
                    if percent is not None
                    else "  ⏳ 屏幕显示正在更新资源..."
                )
                if report != last_report:
                    print(report)
                    last_report = report
                if self.handle_game_update_prompt():
                    print("  👆 已点击更新确认")
                    sleep_func(min(5, max(0.1, deadline - monotonic_func())))
                else:
                    sleep_func(min(poll_interval, max(0.1, deadline - monotonic_func())))
                continue

            if state is not None:
                clean_reads = 0
                if not state.get("updating"):
                    print(
                        f"  ✅ 资源更新完成: v{state.get('target_version')} "
                        f"({state.get('done')}/{state.get('total')} 必需包)"
                    )
                    return True
                system_seen_updating = True
                report = (
                    f"  ⏳ 系统更新状态: v{state.get('target_version')} "
                    f"{state.get('percent'):.1f}% "
                    f"({state.get('done')}/{state.get('total')} 必需包)"
                )
                if report != last_report:
                    print(report)
                    last_report = report
                if self.handle_game_update_prompt():
                    print("  👆 已点击更新确认")
                    sleep_func(min(5, max(0.1, deadline - monotonic_func())))
                    continue
            else:
                clean_reads += 1
                # Once ADB proved an update was running, don't proceed merely
                # because a transient cat/OCR read failed.  Require several
                # consecutive clean reads or wait until the configured timeout.
                clean_reads_needed = 10 if system_seen_updating else 2
                if clean_reads >= clean_reads_needed:
                    print("  ✅ 未检测到资源更新，继续启动流程")
                    return True
                print(
                    f"  ℹ️ 未读取到更新状态，二次确认中... "
                    f"({clean_reads}/{clean_reads_needed})"
                )

            sleep_func(min(poll_interval, max(0.1, deadline - monotonic_func())))
