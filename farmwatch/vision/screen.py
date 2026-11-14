"""画面采集、固定画布识别和作物文字解析。"""

from __future__ import annotations
import os
import re
import math
import stat
import hashlib
import subprocess
import tempfile
import uuid
import shutil as _shutil
from pathlib import Path
from datetime import timedelta
import cv2
from farmwatch.domain.results import ScreenshotCaptureError
from .controls import CONTROLS, FRAME_SIZE


class Recognition:
    def _normalize_screenshot_archive_label(self, label):
        """只保留已知归档标签，避免生成不可清理的文件名。"""
        if label in {'farm_entry', 'harvest'}:
            return label
        if label == 'error' or 'fail' in str(label):
            return 'error'
        return 'capture'

    def _archive_enabled_for(self, label):
        return self.settings.screenshots_enabled and (
            self.settings.screenshots_mode == 'all'
            or self._normalize_screenshot_archive_label(label) != 'capture'
        )

    def archive_current(self, label):
        if not self._archive_enabled_for(label):
            return None
        staged = None
        try:
            staged, final = self._stage_screenshot_archive(self.SCREENSHOT_PATH, label)
            if staged is not None:
                os.replace(staged, final)
                return str(final)
        except OSError as exc:
            print(f'⚠️ 截图归档失败: {exc}')
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        return None

    def maintenance(self, *, force=False, include_login=True):
        """每十分钟清理受控归档；不删除未知文件、目录或符号链接。"""
        now = self.time.monotonic()
        root = str(self.SCREENSHOT_ARCHIVE_DIR)
        if not force and root == getattr(self, '_cleanup_root', None) and now - self._last_archive_cleanup < 600:
            return
        self._last_archive_cleanup, self._cleanup_root = now, root
        self._cleanup_screenshot_archive()
        if not include_login:
            return
        directory = self.SCRIPT_DIR / 'tmp' / 'login_diagnostics'
        cutoff = self.time.time() - self.SCREENSHOT_ARCHIVE_RETENTION_SECONDS
        pattern = re.compile(r'^\d{8}_\d{6}_\d{6}-[A-Za-z0-9_.-]+\.(png|txt)$')
        try:
            for path in directory.iterdir():
                if not pattern.fullmatch(path.name):
                    continue
                metadata = path.stat(follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode) and metadata.st_mtime < cutoff:
                    path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f'⚠️ 登录诊断清理失败: {exc}')

    def _stage_screenshot_archive(self,
        source_path,
        archive_label="capture",
        archive_dir=None,
        now=None,
    ):
        """把已验证截图复制到同目录的私有临时归档文件。"""
        if not self._archive_enabled_for(archive_label):
            return None, None
        archive_root = Path(
            self.SCREENSHOT_ARCHIVE_DIR if archive_dir is None else archive_dir
        )
        staged_path = None
        try:
            archive_root.mkdir(parents=True, exist_ok=True)
            captured_at = self.time.time() if now is None else now
            timestamp = self._DATETIME_TYPE.fromtimestamp(captured_at).strftime(
                "%Y%m%d_%H%M%S_%f"
            )
            label = self._normalize_screenshot_archive_label(archive_label)
            final_path = archive_root / (
                f"{self.SCREENSHOT_ARCHIVE_PREFIX}{label}_{timestamp}_"
                f"{uuid.uuid4().hex[:8]}.png"
            )
            descriptor, staged_name = tempfile.mkstemp(
                prefix=f".{final_path.name}.",
                suffix=".tmp",
                dir=str(archive_root),
            )
            staged_path = Path(staged_name)
            os.close(descriptor)
            _shutil.copyfile(str(source_path), str(staged_path))
            if not staged_path.is_file() or staged_path.stat().st_size <= 0:
                raise OSError("staged screenshot archive is empty")
            return staged_path, final_path
        except Exception:
            if staged_path is not None:
                try:
                    staged_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _cleanup_screenshot_archive(self, archive_dir=None, now=None):
        """删除严格早于保留期限且名称受控的普通截图归档文件。"""
        import re

        archive_root = Path(
            self.SCREENSHOT_ARCHIVE_DIR if archive_dir is None else archive_dir
        )
        current_time = self.time.time() if now is None else now
        cutoff = current_time - self.SCREENSHOT_ARCHIVE_RETENTION_SECONDS
        filename_pattern = re.compile(
            r"^device_(capture|farm_entry|harvest|error)_"
            r"\d{8}_\d{6}_\d{6}_[0-9a-f]{8}\.png$"
        )
        removed = []
        try:
            candidates = list(archive_root.iterdir())
        except FileNotFoundError:
            return removed
        except OSError as exc:
            print(f"⚠️ 截图归档清理失败: {exc}")
            return removed

        for candidate in candidates:
            if filename_pattern.fullmatch(candidate.name) is None:
                continue
            try:
                file_stat = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_mtime >= cutoff:
                    continue
                candidate.unlink()
                removed.append(candidate)
            except FileNotFoundError:
                continue
            except OSError as exc:
                print(f"⚠️ 截图归档清理失败: {candidate}: {exc}")
        return removed

    def screenshot(self, path=None, *, archive_label="capture"):
        """有限重试并原子获取新截图；失败时绝不返回或删除共享旧目标。"""
        if path is None:
            path = self.SCREENSHOT_PATH
        target = Path(path)
        remote_path = "/sdcard/screen.png"
        failures = []

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise ScreenshotCaptureError(
                f"cannot prepare screenshot directory: {exc}"
            ) from exc

        for attempt in range(1, self.SCREENSHOT_MAX_ATTEMPTS + 1):
            staged_archive = None
            final_archive = None
            temp_path = target.with_name(
                f".{target.stem}.{uuid.uuid4().hex}.png"
            )
            try:
                capture_result = subprocess.run(
                    [self.ADB, "-s", self.DEVICE, "shell", f"screencap -p {remote_path}"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    creationflags=self._SUBPROCESS_FLAGS,
                )
                if capture_result.returncode != 0:
                    diagnostic = (
                        str(capture_result.stderr or "").strip()
                        or str(capture_result.stdout or "").strip()
                        or f"ADB 返回码 {capture_result.returncode}"
                    )
                    raise RuntimeError(f"screencap failed: {diagnostic}")

                pull_result = subprocess.run(
                    [self.ADB, "-s", self.DEVICE, "pull", remote_path, str(temp_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    creationflags=self._SUBPROCESS_FLAGS,
                )
                if pull_result.returncode != 0:
                    diagnostic = (
                        str(pull_result.stderr or "").strip()
                        or str(pull_result.stdout or "").strip()
                        or f"ADB 返回码 {pull_result.returncode}"
                    )
                    raise RuntimeError(f"screenshot pull failed: {diagnostic}")

                if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                    raise RuntimeError("screenshot pull produced no new local file")

                image = cv2.imread(str(temp_path))
                if image is None or image.size == 0:
                    raise RuntimeError("pulled screenshot is not readable by OpenCV")

                height, width = image.shape[:2]
                if height > width:
                    image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
                    if not cv2.imwrite(str(temp_path), image):
                        raise OSError("failed to write rotated screenshot")
                    rotated_image = cv2.imread(str(temp_path))
                    if rotated_image is None or rotated_image.size == 0:
                        raise OSError("rotated screenshot is not readable by OpenCV")

                try:
                    staged_archive, final_archive = self._stage_screenshot_archive(
                        temp_path,
                        archive_label=archive_label,
                    )
                except Exception as exc:
                    staged_archive = None
                    final_archive = None
                    print(f"⚠️ 截图归档失败: {exc}")

                # 临时文件与目标位于同一目录，os.replace 在同一文件系统内原子替换。
                os.replace(str(temp_path), str(target))
                self._remember_frame(target, image)

                if staged_archive is not None and final_archive is not None:
                    try:
                        os.replace(str(staged_archive), str(final_archive))
                        staged_archive = None
                    except Exception as exc:
                        print(f"⚠️ 截图归档发布失败: {exc}")
                        try:
                            staged_archive.unlink(missing_ok=True)
                        except OSError:
                            pass
                        staged_archive = None
                    else:
                        try:
                            self.maintenance(include_login=False)
                        except Exception as exc:
                            print(f"⚠️ 截图归档清理失败: {exc}")
                return path
            except Exception as exc:
                failures.append(f"attempt {attempt}: {exc}")
                if (
                    self._is_adb_transport_failure(exc)
                    and attempt < self.SCREENSHOT_MAX_ATTEMPTS
                ):
                    self.recover_adb_connection()
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if staged_archive is not None:
                    try:
                        staged_archive.unlink(missing_ok=True)
                    except OSError:
                        pass
                if attempt < self.SCREENSHOT_MAX_ATTEMPTS:
                    self.time.sleep(self.SCREENSHOT_RETRY_INTERVAL)

        summary = "; ".join(failures)
        raise ScreenshotCaptureError(
            f"screenshot failed after {self.SCREENSHOT_MAX_ATTEMPTS} attempts: {summary}"
        )

    def _image_key(self, path):
        path = Path(path).resolve()
        metadata = path.stat()
        return str(path), metadata.st_mtime_ns, metadata.st_size, metadata.st_ino

    def _read_image(self, path):
        try:
            key = self._image_key(path)
        except (OSError, TypeError, ValueError):
            return cv2.imread(str(path))
        cached = self._frame_cache.get(key)
        if cached is not None:
            return cached
        image = cv2.imread(str(path))
        if image is not None:
            if len(self._frame_cache) >= 4:
                self._frame_cache.clear()
            self._frame_cache[key] = image
        return image

    def _remember_frame(self, path, image):
        try:
            key = self._image_key(path)
            self._frame_cache.clear()
            self._frame_cache[key] = image
        except OSError:
            pass

    def _matching_roi(self, control_id):
        control = CONTROLS.get(control_id)
        return control.region if control else (0, 0, 1, 1)

    def locate_control(self, control_id, screenshot_path, threshold=.80):
        """仅在固定画布的控件区域识别；未知控件和其他尺寸均不产生点击坐标。"""
        control = CONTROLS.get(control_id)
        frame = self._read_image(screenshot_path)
        if control is None or frame is None or frame.shape[:2] != FRAME_SIZE[::-1]:
            return None
        left, top, right, bottom = control.region
        x0, y0, x1, y1 = int(left*1280), int(top*720), int(right*1280), int(bottom*720)
        if control.asset is None:
            try:
                cache_key = self._image_key(screenshot_path)
            except (OSError, ValueError, TypeError):
                cache_key = None
            lines = self._control_ocr_cache.get(cache_key) if cache_key is not None else None
            if lines is None:
                if float(frame.std()) < 2:
                    return None
                result, _ = self.get_ocr()(frame, use_cls=False)
                lines = [(box, ''.join(str(text).split()), float(score))
                         for box, text, score in (result or []) if float(score) >= .80]
                if cache_key is not None:
                    self._control_ocr_cache.clear()
                    self._control_ocr_cache[cache_key] = lines
            context = ' '.join(text for _, text, _ in lines)
            if control.context and not any(word in context for word in control.context):
                return None
            hits = []
            for box, text, score in lines:
                if text not in control.labels or score < max(.80, threshold):
                    continue
                cx = sum(float(point[0]) for point in box) / len(box)
                cy = sum(float(point[1]) for point in box) / len(box)
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    hits.append({'x': round(cx), 'y': round(cy), 'score': score, 'method': 'text'})
            # An ambiguous page must be inspected instead of choosing a random button.
            return hits[0] if len(hits) == 1 else None
        path = self.TEMPLATE_DIR / control.asset
        try:
            identity = self._image_key(path)
        except OSError:
            return None
        template = self._template_cache.get(identity)
        if template is None:
            template = cv2.imread(str(path))
            if template is None:
                return None
            self._template_cache[identity] = template
        region = frame[y0:y1, x0:x1]
        height, width = template.shape[:2]
        if height > region.shape[0] or width > region.shape[1] or float(template.std()) < 2:
            return None
        response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, origin = cv2.minMaxLoc(response)
        if not math.isfinite(confidence) or confidence < threshold:
            return None
        return {'x': x0 + origin[0] + width//2, 'y': y0 + origin[1] + height//2,
                'score': confidence}

    def locate_login_control(self, template_name, screenshot_path):
        """匹配登录模板并拒绝落在错误 ROI 的结果。

        登录页面上可能同时出现相似按钮或历史残影，因此模板匹配结果还要
        经过模板规格中定义的归一化 ROI 校验。任何未知模板、截图读取失败或
        匹配结果格式异常都安全地视为未检测到。
        """
        spec = self.LOGIN_CONTROLS.get(template_name)
        if not spec:
            return None

        try:
            threshold = spec["threshold"]
            roi = spec["roi"]
            match = self.locate_control(template_name, screenshot_path, threshold)
        except Exception as exc:
            # 模板匹配本身不应让登录恢复流程崩溃。
            print(f"  [WARN] login template '{template_name}' detection failed: {exc}")
            return None

        if match is None:
            return None

        try:
            image = self._read_image(screenshot_path)
            if image is None:
                return None
            image_height, image_width = image.shape[:2]
            if image_width <= 0 or image_height <= 0:
                return None

            # locate_control 返回的是模板中心点像素坐标。
            normalized_x = float(match["x"]) / float(image_width)
            normalized_y = float(match["y"]) / float(image_height)
            x_min, y_min, x_max, y_max = roi
            in_roi = (
                float(x_min) <= normalized_x <= float(x_max)
                and float(y_min) <= normalized_y <= float(y_max)
            )
            if in_roi:
                return match

            print(
                f"  [WARN] login template '{template_name}' match "
                f"({normalized_x:.3f}, {normalized_y:.3f}) is outside ROI"
            )
        except Exception as exc:
            print(f"  [WARN] login template '{template_name}' ROI validation failed: {exc}")
        return None

    def _screen_signature(self, path):
        """返回截图字节的 SHA-256；文件不可读时返回 ``None``。"""
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except (OSError, TypeError, ValueError):
            return None
        return digest.hexdigest()

    def _login_match_center_moved(self,
        before_match,
        after_match,
        tolerance_px=None,
    ):
        """QQ 授权模板中心位移超过抖动容差时返回 ``True``。"""
        if tolerance_px is None:
            tolerance_px = self.LOGIN_QQ_AGREE_SAME_CENTER_TOLERANCE_PX
        try:
            dx = float(after_match["x"]) - float(before_match["x"])
            dy = float(after_match["y"]) - float(before_match["y"])
            return math.hypot(dx, dy) > float(tolerance_px)
        except (KeyError, TypeError, ValueError):
            return False

    def _save_login_diagnostic(self,
        state,
        screenshot_path=None,
        *,
        error=None,
        screenshot_is_stale=False,
    ):
        """保存登录诊断截图或错误文本，失败时仅打印警告。"""
        if not self.settings.screenshots_enabled:
            return None
        if screenshot_path is None:
            screenshot_path = self.SCREENSHOT_PATH
        try:
            source = Path(screenshot_path)
            diagnostic_dir = Path(self.SCRIPT_DIR) / "tmp" / "login_diagnostics"
            diagnostic_dir.mkdir(parents=True, exist_ok=True)

            # 状态来自内部有限状态机，但仍做文件名清理以避免路径穿越或非法字符。
            import re

            safe_state = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(state)).strip("._")
            safe_state = safe_state or "unknown"
            timestamp = self.datetime.now().strftime("%Y%m%d_%H%M%S_%f")

            def unique_destination(marker, extension):
                marker_part = f"-{marker}" if marker else ""
                destination = diagnostic_dir / (
                    f"{timestamp}-{safe_state}{marker_part}{extension}"
                )
                suffix = 1
                while destination.exists():
                    destination = diagnostic_dir / (
                        f"{timestamp}-{safe_state}{marker_part}-{suffix}{extension}"
                    )
                    suffix += 1
                return destination

            if error is None:
                destination = unique_destination(None, ".png")
                # copy2 保留截图原始字节，诊断文件即使不是由 cv2 生成也可保存。
                _shutil.copy2(str(source), str(destination))
                return str(destination)

            artifact_note = "screenshot: unavailable"
            if source.is_file():
                marker = "last-good-stale" if screenshot_is_stale else "error-frame"
                artifact_destination = unique_destination(marker, ".png")
                try:
                    _shutil.copy2(str(source), str(artifact_destination))
                    if screenshot_is_stale:
                        artifact_note = (
                            "last-good/stale screenshot: "
                            f"{artifact_destination.name}"
                        )
                    else:
                        artifact_note = f"error-frame screenshot: {artifact_destination.name}"
                except Exception as artifact_exc:
                    artifact_note = f"screenshot artifact copy failed: {artifact_exc}"

            text_destination = unique_destination("error", ".txt")
            diagnostic_text = "\n".join(
                [
                    f"timestamp: {self.datetime.now().isoformat()}",
                    f"state: {state}",
                    f"error_type: {type(error).__name__}",
                    f"error: {error}",
                    artifact_note,
                    "",
                ]
            )
            text_destination.write_text(diagnostic_text, encoding="utf-8")
            return str(text_destination)
        except Exception as exc:
            print(f"  [WARN] saving login diagnostic failed: {exc}")
            return None

    def control_visible(self, template_name, screenshot_path, threshold=0.6):
        """检查模板是否存在"""
        return self.locate_control(template_name, screenshot_path, threshold) is not None

    def press_control(self, template_name, screenshot_path, threshold=0.6, label=""):
        """匹配并点击模板"""
        result = self.locate_control(template_name, screenshot_path, threshold)
        if result:
            self.tap(result["x"], result["y"], label or template_name)
            return True
        return False

    def press_first_control(self,
        template_names, screenshot_path, threshold=0.6, label="", roi=None
    ):
        """按调用方声明的优先级寻找可用控件。"""
        for template_name in template_names:
            match = self.locate_control(template_name, screenshot_path, threshold)
            if match is None:
                continue
            if roi is not None:
                image = self._read_image(screenshot_path)
                if image is None:
                    continue
                height, width = image.shape[:2]
                x_min, y_min, x_max, y_max = roi
                normalized = (match["x"] / width, match["y"] / height)
                if not (x_min <= normalized[0] <= x_max and y_min <= normalized[1] <= y_max):
                    print(f"  ⚠️ '{template_name}' 匹配位置不在按钮区域，忽略")
                    continue
            self.tap(match["x"], match["y"], label or template_name)
            return True
        return False

    def locate_start_control(self, screenshot_path):
        """开始游戏必须命中已采集的文字控件，背景颜色不作为可点击证据。"""
        match = self.locate_control('session.start', screenshot_path, .80)
        if match and 320 <= match['x'] <= 960 and 432 <= match['y'] <= 648:
            return match
        return None

    def locate_farm_action(self, screenshot_path=None):
        """Find the one-click button while rejecting scene-text false matches."""
        if screenshot_path is None:
            screenshot_path = self.SCREENSHOT_PATH
        match = self.locate_control(
            self.FARM_ACTION_CONTROL, screenshot_path, self.FARM_ACTION_THRESHOLD
        )
        if match is None:
            return None

        image = self._read_image(screenshot_path)
        if image is None:
            return None
        height, width = image.shape[:2]
        x_ratio = match["x"] / width
        y_ratio = match["y"] / height
        x1, y1, x2, y2 = self.FARM_ACTION_ROI
        if not (x1 <= x_ratio <= x2 and y1 <= y_ratio <= y2):
            print(
                f"  ⚠️ '{self.FARM_ACTION_CONTROL}' 匹配位置不在一键务农区域，"
                f"忽略 ({x_ratio:.2f},{y_ratio:.2f})"
            )
            return None
        return match

    def move_joystick(self, angle_deg, distance=200, duration_ms=1500, center=None):
        """向指定角度推动摇杆"""
        cx, cy = center or self.JOYSTICK_CENTER
        angle_rad = math.radians(angle_deg)
        tx = int(cx + distance * math.cos(angle_rad))
        ty = int(cy - distance * math.sin(angle_rad))
        self.swipe(cx, cy, tx, ty, duration_ms)
        print(f"  🎮 摇杆 ({cx},{cy})→({tx},{ty}) {angle_deg}° {duration_ms}ms")

    def reset_position(self):
        """用新截图定位右下角复原按钮；ADB 未接受点击时不继续走位。"""
        print("  🔄 复原站位...")
        self.screenshot(self.SCREENSHOT_PATH)
        match = self.locate_control("farm.reset", self.SCREENSHOT_PATH, 0.5)
        if match is None:
            print("  ⚠️ 未找到右下角复原按钮")
            return False
        x, y = match["x"], match["y"]
        print(f"  👆 tap ({x}, {y}) [复原站位]")
        try:
            clicked = self.adb_shell_result(f"input tap {x} {y}")
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  ⚠️ 复原点击异常: {exc}")
            return False
        if clicked.returncode != 0:
            print(f"  ⚠️ 复原点击失败: rc={clicked.returncode}")
            return False
        print("  ⏳ 等待复原5秒...")
        self.time.sleep(5)
        return True

    def parse_crop_name_text(self, text):
        """Extract a known crop name from OCR text, tolerating spaces."""
        if not isinstance(text, str):
            return None
        compact = "".join(text.split())
        if not compact:
            return None
        variant_base = self._strip_crop_variant(compact)
        if variant_base != compact and variant_base in self.KNOWN_CROP_NAMES:
            return variant_base
        candidates = [name for name in self.KNOWN_CROP_NAMES if name in compact]
        if not candidates:
            return None
        return max(candidates, key=len)

    def _ocr_box_metrics(self, box):
        """Return an OCR quadrilateral's axis-aligned bounds, or None."""
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            return None
        try:
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
        except (TypeError, ValueError, IndexError):
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def _dynamic_crop_text(self, text):
        """Normalize a positional crop-name candidate without using a whitelist."""
        import re

        if not isinstance(text, str):
            return None
        candidate = "".join(re.findall(r"[\u4e00-\u9fff]+", text))
        if not candidate or candidate in self.CROP_NAME_UI_WORDS:
            return None
        if any(
            word in candidate
            for word in ("成熟", "升级", "农场", "经验", "收获", "等级", "水分")
        ):
            return None
        return candidate

    def detect_crop_name_from_ocr(self, result):
        """Find an arbitrary crop name immediately above a water-count OCR box."""
        import re

        entries = []
        for line in result or []:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            metrics = self._ocr_box_metrics(line[0])
            if metrics is not None:
                entries.append((metrics, str(line[1])))

        anchors = [
            entry
            for entry in entries
            if re.fullmatch(r"[0-3]\s*/\s*3", entry[1].strip())
        ]
        for (ax1, ay1, ax2, ay2), _ in anchors:
            anchor_x = (ax1 + ax2) / 2
            anchor_h = max(1.0, ay2 - ay1)
            candidates = []
            for metrics, text in entries:
                x1, y1, x2, y2 = metrics
                candidate = self._dynamic_crop_text(text)
                if candidate is None or y2 >= (ay1 + ay2) / 2:
                    continue
                center_x = (x1 + x2) / 2
                if abs(center_x - anchor_x) > max(200.0, (ax2 - ax1) * 6):
                    continue
                if ay1 - y2 > anchor_h * 7:
                    continue
                candidates.append(
                    (ay1 - y2, (y1 + y2) / 2, x1, x2, candidate, y2 - y1)
                )

            if not candidates:
                continue
            candidates.sort(key=lambda item: item[0])
            best = candidates[0]
            same_row = [
                item
                for item in candidates
                if abs(item[1] - best[1]) <= max(item[5], best[5]) * 0.6
            ]
            same_row.sort(key=lambda item: item[2])

            # Keep only adjacent fragments from the selected row. This supports
            # OCR splitting "猕猴桃" without joining unrelated text far away.
            clusters = []
            for item in same_row:
                if not clusters:
                    clusters.append([item])
                    continue
                previous = clusters[-1][-1]
                max_gap = max(previous[5], item[5]) * 1.5
                if item[2] - previous[3] <= max_gap:
                    clusters[-1].append(item)
                else:
                    clusters.append([item])
            best_cluster = min(
                clusters,
                key=lambda cluster: min(item[0] for item in cluster),
            )
            return "".join(item[4] for item in best_cluster)
        return None

    def read_crop_name_region(self, image):
        """OCR only the crop-card header, independent of the fraction counter."""
        if image is None or image.size == 0:
            return None
        height, width = image.shape[:2]
        x1, y1, x2, y2 = self.CROP_NAME_ROI
        header = image[int(height*y1):int(height*y2), int(width*x1):int(width*x2)]
        if header.size == 0:
            return None
        try:
            enlarged = cv2.resize(header, None, fx=self.CROP_NAME_OCR_SCALE,
                                 fy=self.CROP_NAME_OCR_SCALE, interpolation=cv2.INTER_CUBIC)
            result, _ = self.get_ocr()(enlarged)
        except Exception as exc:
            print(f"  ⚠️ 作物名称区域 OCR 失败，使用兼容识别: {exc}")
            return None

        fragments = []
        for line in result or []:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            text = self.normalize_crop_name(line[1])
            if (not text or not re.fullmatch(r"[\u4e00-\u9fff]{1,12}", text)
                    or text in self.CROP_NAME_UI_WORDS or text in self.UNKNOWN_CROP_NAMES
                    or text in {"巨大化", "璀璨", "绿光", "青玉", "桃霞"}
                    or self._dynamic_crop_text(text) is None):
                continue
            confidence = line[2] if len(line) > 2 else None
            if isinstance(confidence, (int, float)) and confidence < 0.6:
                continue
            bounds = self._ocr_box_metrics(line[0])
            fragments.append((bounds, text))
        if not fragments:
            return None
        if len(fragments) == 1:
            name = fragments[0][1]
        else:
            # Merge adjacent fragments of the same heading, not unrelated OCR rows.
            if any(bounds is None for bounds, _ in fragments):
                return None
            fragments.sort(key=lambda item: item[0][0])
            for (left, _), (right, _) in zip(fragments, fragments[1:]):
                h = max(1.0, left[3]-left[1], right[3]-right[1])
                if (abs((left[1]+left[3]-right[1]-right[3])/2) > h*0.6
                        or right[0]-left[2] > h*2):
                    return None
            name = "".join(text for _, text in fragments)
        name = self.normalize_crop_name(name)
        if name is None or len(name) > 12:
            return None
        print(f"  🌾 独立名称区域 OCR: {name}")
        return name

    def read_crop_state(self, screenshot_path, reference_time=None):
        """Read the name from its own header ROI, and maturity from the broad ROI."""
        img = self._read_image(screenshot_path)
        if img is None:
            return {"crop_name": None, "maturity_time": None, "is_mature": False}

        h, w = img.shape[:2]
        # The crop label is above the maturity line; include both in one generous
        # left-panel ROI so OCR can identify the same crop across rounds.
        roi = img[0:int(h * 0.75), 0:int(w * 0.5)]
        try:
            result, _ = self.get_ocr()(roi)
        except Exception as exc:
            print(f"  ⚠️ OCR失败: {exc}")
            result = []

        all_text = " ".join(line[1] for line in result) if result else ""
        if all_text:
            print(f"  📝 OCR文本: {all_text}")
        crop_name = (self.read_crop_name_region(img)
                     or self.detect_crop_name_from_ocr(result)
                     or self.parse_crop_name_text(all_text))
        if "可收获" in all_text or "已成熟" in all_text:
            return {"crop_name": crop_name, "maturity_time": None, "is_mature": True}

        maturity_time = (
            self.parse_maturity_text(all_text, reference_time=reference_time)
            if all_text else None
        )
        if maturity_time:
            hour, minute, day_offset = maturity_time
            day_text = (
                "后天 " if day_offset == 2
                else "明天 " if day_offset == 1
                else "今天 " if day_offset == 0
                else ""
            )
            print(f"  🕐 OCR识别: {day_text}{hour}点{minute:02d}分")
        return {
            "crop_name": crop_name,
            "maturity_time": maturity_time,
            "is_mature": False,
        }

    def parse_maturity_text(self, text, reference_time=None):
        """解析成熟时间文本，返回 (hour, minute, day_offset) 或 None。

        除支持 ``HH:MM成熟`` 外，也支持游戏里的倒计时格式，例如
        ``14小时39分钟后成熟``。倒计时需要参考时间来换算成钟点时间。
        """
        import re

        maturity_matches = list(re.finditer(
            r'(?<!\d)(?:(今天|明天|后天)\s*)?(\d{1,2})[:\uff1a](\d{2})(?!\d)\s*成熟',
            text,
        ))
        candidates = maturity_matches or re.finditer(
            r'(?<!\d)(?:(今天|明天|后天)\s*)?(\d{1,2})[:\uff1a](\d{2})(?!\d)',
            text,
        )

        for match in candidates:
            day_text, hour_text, minute_text = match.groups()
            hour = int(hour_text)
            minute = int(minute_text)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                continue

            if day_text == "后天" or (day_text is None and "后天" in text):
                day_offset = 2
            elif day_text == "明天" or (day_text is None and "明天" in text):
                day_offset = 1
            elif day_text == "今天" or (day_text is None and "今天" in text):
                day_offset = 0
            else:
                day_offset = None
            return hour, minute, day_offset

        duration_match = re.search(
            r"(?<![\d./])(?:(\d+)\s*天)?(?:(\d+)\s*小时)?"
            r"(?:(\d+)\s*分(?:钟)?)?(?:(\d+)\s*秒)?\s*后\s*成熟",
            text,
        )
        if duration_match:
            day_text, hour_text, minute_text, second_text = duration_match.groups()
            duration = timedelta(
                days=int(day_text or 0),
                hours=int(hour_text or 0),
                minutes=int(minute_text or 0),
                seconds=int(second_text or 0),
            )
            base = reference_time or self.datetime.now()
            target = base + duration
            day_offset = (target.date() - base.date()).days
            return target.hour, target.minute, day_offset

        return None

    def resolve_maturity_datetime(self, first_water_time, maturity_time):
        """根据 OCR 成熟时间和日期提示构造完整成熟时间。"""
        hour, minute, day_offset = maturity_time
        maturity_dt = first_water_time.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

        if day_offset is not None:
            return maturity_dt + timedelta(days=day_offset)
        if maturity_dt <= first_water_time:
            maturity_dt += timedelta(days=1)
        return maturity_dt

    def get_ocr(self):
        if self._ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_engine = RapidOCR()
        return self._ocr_engine

    def read_maturity_time(self, screenshot_path, reference_time=None):
        """从截图中读取成熟时间及日期提示（如明天16:46成熟）。
        返回: ((hour, minute, day_offset), is_mature)
            - 识别到时间: ((hour, minute, day_offset), False)
            - 作物已成熟可收获: (None, True)
            - 无法识别: (None, False)
        """
        img = self._read_image(screenshot_path)
        if img is None:
            return None, False

        # 裁剪成熟时间区域（左侧面板，覆盖"xx:xx成熟"和"可收获"区域）
        h, w = img.shape[:2]
        roi = img[h//3:2*h//3, 0:w//3]

        ocr = self.get_ocr()
        result, _ = ocr(roi)

        if result:
            all_text = " ".join([line[1] for line in result])
            print(f"  📝 OCR文本: {all_text}")

            # 检查是否显示"可收获"或"已成熟"（作物已经成熟）
            if "可收获" in all_text or "已成熟" in all_text:
                return None, True

            maturity_time = self.parse_maturity_text(
                all_text, reference_time=reference_time
            )
            if maturity_time:
                hour, minute, day_offset = maturity_time
                day_text = (
                    "后天 " if day_offset == 2
                    else "明天 " if day_offset == 1
                    else "今天 " if day_offset == 0
                    else ""
                )
                print(f"  🕐 OCR识别: {day_text}{hour}点{minute:02d}分")
                return maturity_time, False

        return None, False

    def read_harvest_info(self, screenshot_path):
        """从收获弹窗截图中OCR识别收获信息
        返回: {"exp": int, "crops": {作物名: 数量}} 或 None
        """
        import re

        img = self._read_image(screenshot_path)
        if img is None:
            return None

        h, w = img.shape[:2]
        # 收获弹窗通常在屏幕中央区域
        roi = img[int(h*0.2):int(h*0.8), int(w*0.2):int(w*0.8)]

        try:
            ocr = self.get_ocr()
            result, _ = ocr(roi)
        except Exception as e:
            print(f"  ⚠️ OCR失败: {e}")
            return None

        if not result:
            return None

        all_text = " ".join([line[1] for line in result])
        print(f"  📝 OCR文本: {all_text}")

        harvest = {"exp": 0, "crops": {}}

        # 识别经验
        # 匹配: "XP" + 数字, "农场经验" + 数字, "+数字经验", "经验+数字"
        exp_match = re.search(r'XP\s*(\d+)', all_text)
        if not exp_match:
            exp_match = re.search(r'(\d+)\s*XP', all_text)
        if not exp_match:
            exp_match = re.search(r'[+＋](\d+)\s*[经経]验', all_text)
        if not exp_match:
            exp_match = re.search(r'[经経]验\s*[+＋](\d+)', all_text)
        if exp_match:
            harvest["exp"] = int(exp_match.group(1))

        # 识别作物名和数量
        # OCR输出格式: 作物名和数字可能在不同行，按x坐标排序配对
        crop_names = {"番茄", "洋葱", "小麦", "土豆", "胡萝卜", "白菜", "玉米",
                      "南瓜", "草莓", "西瓜", "辣椒", "茄子", "黄瓜", "大豆"}

        # 提取所有文字及其位置
        items = []
        for line in result:
            text = line[1]
            x = line[0][0][0]  # 左上角x坐标
            y = line[0][0][1]  # 左上角y坐标
            items.append({"text": text, "x": x, "y": y})

        # 按y坐标排序，找数字行和作物名行的配对
        numbers = []  # [(x, value)]
        crops_found = []  # [(x, name)]

        for item in items:
            text = item["text"].strip()
            if text in crop_names:
                crops_found.append((item["x"], text))
            elif text.isdigit() and int(text) > 0:
                numbers.append((item["x"], int(text)))

        # 配对: 每个作物名找最近的数字（同y坐标或下方）
        for cx, cname in crops_found:
            best_num = 0
            best_dist = float("inf")
            for nx, nval in numbers:
                dist = abs(nx - cx)
                if dist < best_dist:
                    best_dist = dist
                    best_num = nval
            if best_num > 0:
                harvest["crops"][cname] = harvest["crops"].get(cname, 0) + best_num

        if harvest["exp"] > 0 or harvest["crops"]:
            return harvest
        return None
