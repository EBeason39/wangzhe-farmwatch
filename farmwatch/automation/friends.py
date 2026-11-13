"""Pinned-friend farm visits, using the existing ADB and OCR adapters.

All recognition coordinates refer to the recorded 1280x720 layout. A click
never proves a harvest: each stop needs a game response or a counter change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import re
import time
import uuid

import cv2
import numpy as np


CHINA_TZ = timezone(timedelta(hours=8))
SIZE = (1280, 720)
ROOT = Path(__file__).resolve().parents[2]
ROUTE_PATH = ROOT / "resources" / "routes" / "pinned-friends.json"


def take_blocked_until(now=None):
    """The game explicitly forbids taking crops from 00:00 to 08:00 China time."""
    now = now or datetime.now(CHINA_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CHINA_TZ)
    now = now.astimezone(CHINA_TZ)
    return now.replace(hour=8, minute=0, second=0, microsecond=0) if now.hour < 8 else None


def game_day(now=None):
    """An explicit China-time day, resetting at 08:00, including on Windows."""
    now = now or datetime.now(CHINA_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CHINA_TZ)
    return (now.astimezone(CHINA_TZ) - timedelta(hours=8)).date().isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class DailyLimit:
    """Persist observations, never infer a precise limit from click totals."""

    def __init__(self, path, scope, now=None):
        self.path, self.scope = Path(path), scope
        try:
            self.all = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.all, dict):
                self.all = {}
        except (OSError, ValueError):
            self.all = {}
        self.refresh(now)

    def refresh(self, now=None):
        day = game_day(now)
        value = self.all.get(self.scope, {})
        if not isinstance(value, dict) or value.get("day") != day:
            value = {"day": day, "exhausted": False, "remaining": None}
        self.data = value
        self.all[self.scope] = value

    @property
    def exhausted(self):
        return self.data.get("exhausted") is True

    def observe(self, remaining=None, exhausted=False, now=None):
        self.refresh(now)
        if type(remaining) is int and 0 <= remaining <= 999:
            self.data["remaining"] = remaining
            # A fresh positive game reading also corrects an earlier OCR zero.
            self.data["exhausted"] = remaining == 0
        if exhausted:
            self.data["exhausted"] = True
        atomic_json(self.path, self.all)


def parse_remaining(text):
    text = re.sub(r"\s+", "", text)
    match = re.search(r"剩余[:：]?(\d{1,3})次", text)
    return int(match.group(1)) if match else None


def parse_feedback(text):
    text = re.sub(r"\s+", "", text).replace("：", ":")
    if re.search(r"0{1,2}:00[-—–~～至到]+0?8:00.{0,8}(?:无法|不能|不可)拿取", text):
        return "night_closed"
    # An ordinary 'cannot take' message is NOT evidence of the daily cap.
    if (re.search(r"(?:今日|今天|每日).{0,12}(?:拿取|偷取|次数).{0,12}(?:上限|用完|耗尽)", text)
            or re.search(r"(?:拿取|偷取)次数.{0,6}(?:用完|耗尽|不足)", text)):
        return "exhausted"
    if any(word in text for word in ("已经拿取过", "已拿取过", "已经拿过", "已拿取", "不能再拿取", "不可拿取", "无法拿取", "未成熟", "还未成熟")):
        return "unavailable"
    if any(word in text for word in ("拿取成功", "获得了", "获得作物")):
        return "taken"
    return None


@dataclass
class Friend:
    y: int
    pinned: bool
    available: bool
    identity: np.ndarray
    name: str = ""


@dataclass
class PlotState:
    selected: bool = False
    remaining: int | None = None
    stock: int | None = None
    button: tuple[int, int] | None = None
    disabled: bool = False
    feedback: str | None = None
    text: str = ""
    unripe: bool = False


class Vision:
    def __init__(self, template_dir, ocr):
        self.templates = {}
        for path in Path(template_dir).glob("social-*.png"):
            self.templates[path.stem] = cv2.imread(str(path))
        self.ocr = ocr
        self.hand_edges = None
        template = self.templates.get("social-take-lit")
        if template is not None:
            bright = (cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) > 165).astype(np.uint8)
            _, labels, stats, _ = cv2.connectedComponentsWithStats(bright)
            if len(stats) > 1:
                palm = (labels == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
                inner = ((cv2.erode(palm, np.ones((3, 3), np.uint8)) > 0)
                         & (cv2.erode(palm, np.ones((9, 9), np.uint8)) == 0))
                outer = ((cv2.dilate(palm, np.ones((7, 7), np.uint8)) > 0)
                         & (cv2.dilate(palm, np.ones((3, 3), np.uint8)) == 0))
                shape = palm.astype(np.float32)
                shape -= cv2.GaussianBlur(shape, (0, 0), 3)
                self.hand_edges = shape, inner, outer

    def match(self, image, name, box, threshold=.78):
        template = self.templates.get(name)
        if template is None:
            return None
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
            return None
        response = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(response)
        if score < threshold:
            return None
        return (x1 + loc[0] + template.shape[1] // 2,
                y1 + loc[1] + template.shape[0] // 2)

    def read(self, image, box, scale=2):
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        # Game UI text is upright. The orientation classifier can rotate a
        # symmetric stock value such as 90/90 into the high-confidence 06/06.
        result, _ = self.ocr(cv2.resize(crop, None, fx=scale, fy=scale), use_cls=False)
        return " ".join(str(line[1]) for line in (result or []) if float(line[2]) >= .65)

    def panel_open(self, image):
        return bool(self.match(image, "social-game-tab", (700, 0, 835, 65), .72)
                    and self.match(image, "social-collapse", (640, 300, 705, 420), .68))

    def at_friend(self, image):
        return bool(self.match(image, "social-home", (1080, 5, 1240, 80), .78))

    def friends(self, image):
        if not self.panel_open(image):
            return []
        template = self.templates.get("social-visit")
        if template is None:
            return []
        response = cv2.matchTemplate(image[72:625, 1190:1260], template, cv2.TM_CCOEFF_NORMED)
        rows = []
        for _ in range(6):
            _, score, _, loc = cv2.minMaxLoc(response)
            if score < .70:
                break
            y = 72 + loc[1] + template.shape[0] // 2
            response[max(0, loc[1]-65):loc[1]+66, :] = -1
            if not 113 <= y <= 579:
                continue  # Do not click partly visible rows.
            inside = image[y-26:y+27, 1254:1262].astype(float)
            outside = image[y-26:y+27, 1270:1278].astype(float)
            # Yellow row fill ends at x=1266; the underlying scene continues.
            delta = np.median(inside[:, :, 2]) - np.median(outside[:, :, 2])
            warmth = np.median(inside[:, :, 2] - inside[:, :, 0])
            pinned = bool(delta >= 16 and warmth >= 15)
            badge = self.match(image, "social-ready", (714, y+12, 793, y+65), .76)
            # Offline names are dimmed. Normalize only the name strip so the
            # shared avatar, rank and changing scene cannot dominate identity.
            identity = cv2.cvtColor(image[y-31:y+5, 833:1085], cv2.COLOR_BGR2GRAY)
            identity = cv2.normalize(identity, None, 0, 255, cv2.NORM_MINMAX)
            name = ""
            if pinned:
                result, _ = self.ocr(cv2.resize(identity, None, fx=4, fy=4))
                name = "".join(char for line in (result or []) if float(line[2]) >= .80
                               for char in str(line[1]) if char.isalnum()).casefold()
            rows.append(Friend(y, pinned, badge is not None, identity, name))
        return sorted(rows, key=lambda row: row.y)

    @staticmethod
    def same_friend(a, b):
        if a.name and b.name and a.name == b.name:
            return True
        # If OCR fails, only a near-identical name image can suppress a visit.
        if a.identity.shape != b.identity.shape or a.identity.std() < 8 or b.identity.std() < 8:
            return False
        return float(cv2.matchTemplate(a.identity, b.identity, cv2.TM_CCOEFF_NORMED)[0, 0]) >= .985

    def take_button(self, image):
        """Match the palm boundary, excluding scenery inside the translucent UI."""
        if self.hand_edges is None:
            return None, False
        shape, inner, outer = self.hand_edges
        gray = cv2.cvtColor(image[508:627, 985:1165], cv2.COLOR_BGR2GRAY).astype(np.float32)
        edges = gray - cv2.GaussianBlur(gray, (0, 0), 3)
        scores = cv2.matchTemplate(edges, shape, cv2.TM_CCORR_NORMED,
                                   mask=(inner | outer).astype(np.uint8))
        scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        h, w = shape.shape
        matched = edges[y:y+h, x:x+w]
        strength = float(np.median(matched[inner]) - np.median(matched[outer]))
        # Foliage behind a dim palm can raise its edge strength to 20–21.
        # Extend only the disabled band for strong shapes; never lower the
        # clickable threshold, and keep 23–25 uncertain.
        if score >= .80 and strength >= 25:
            return (985 + x + w//2, 508 + y + h//2), False
        dim = (score >= .55 and 3 <= strength <= 20
               or score >= .80 and 20 < strength <= 23)
        return None, bool(dim)

    def plot(self, image):
        if self.panel_open(image) or not self.at_friend(image):
            return PlotState()
        crop_text = self.read(image, (22, 119, 213, 335))
        take_text = self.read(image, (995, 612, 1172, 669), 3)
        message = self.read(image, (325, 180, 990, 360))
        stock_match = re.search(r"(\d{1,3})\s*/\s*\d{1,3}", crop_text.split("可收获")[-1])
        stock = int(stock_match.group(1)) if stock_match and "可收获" in crop_text else None
        selected = bool(stock is not None or re.search(r"成熟|水分|可收获", crop_text))
        button, dim_hand = self.take_button(image)
        disabled = (dim_hand or bool(self.match(image, "social-take-dim", (985, 508, 1165, 627), .80))) and button is None
        feedback = parse_feedback(message + " " + crop_text)
        unripe = bool(re.search(r"后成熟|未成熟", crop_text))
        if feedback is None and unripe:
            feedback = "unavailable"
        return PlotState(selected, parse_remaining(take_text), stock, button, disabled, feedback,
                         " | ".join((crop_text, take_text, message)), unripe)


class StopVisit(RuntimeError):
    pass


def load_route(path=ROUTE_PATH):
    route = json.loads(Path(path).read_text(encoding="utf-8"))
    if route.get("resolution") != list(SIZE):
        raise ValueError("偷菜路线只支持 1280x720")
    stops = route.get("stops", [])
    expected = {f"{side}{row}-{col}" for side in "LR" for row in range(1, 4) for col in range(1, 5)}
    if len(stops) != 24 or {stop["id"] for stop in stops} != expected:
        raise ValueError("路线必须覆盖左右两侧各 3 行、4 列的 24 个不同地块")
    for moves in [route["entry"]] + [stop["moves"] for stop in stops]:
        for angle, duration in moves:
            if not -360 <= angle <= 360 or not 0 < duration <= 6000:
                raise ValueError("无效的路线移动参数")
    return route


class Runner:
    def __init__(self, host, wake_time, *, dry_run=False, max_seconds=None):
        self.host = host
        self.wake_time = wake_time
        self.dry_run = dry_run
        available_seconds = (wake_time-host.datetime.now()).total_seconds() - 15
        if max_seconds is not None:
            available_seconds = min(max_seconds, available_seconds)
        self.deadline = time.monotonic() + max(0, available_seconds)
        self.vision = Vision(host.TEMPLATE_DIR, host.get_ocr())
        self.route = load_route()
        self.limit = DailyLimit(host.DATA_DIR / "friend_visits_state.json", host.MUMU_NAME)
        self.report = {"started": datetime.now(CHINA_TZ).isoformat(), "dry_run": dry_run,
                       "route_version": self.route.get("version"), "visits": [], "status": "started"}
        self.visited = []
        self.pending = self.load_progress("pending_visits")
        self.checked = self.load_progress("checked_friends")

    def load_progress(self, key):
        pending = []
        entries = self.limit.data.get(key, [])
        if not isinstance(entries, list):
            return pending
        plot_ids = {stop["id"] for stop in self.route["stops"]}
        for entry in entries[:128]:
            try:
                if entry["route_version"] != self.route.get("version"):
                    continue
                shape = entry["identity_shape"]
                if shape != [36, 252]:
                    continue
                identity = np.frombuffer(bytes.fromhex(entry["identity"]), dtype=np.uint8).reshape(shape).copy()
                friend = Friend(0, True, False, identity, str(entry.get("name", "")))
                checked_at = entry.get("checked_at", "")
                if not isinstance(checked_at, str):
                    continue
                pending.append({"friend": friend, "taken": set(entry.get("taken_plots", [])) & plot_ids,
                                "checked_at": checked_at})
            except (KeyError, TypeError, ValueError):
                continue
        return pending

    def pending_for(self, friend):
        return next((item for item in self.pending
                     if Vision.same_friend(friend, item["friend"])), None)

    def checked_at(self, friend):
        return next((item["checked_at"] for item in self.checked
                     if Vision.same_friend(friend, item["friend"])), "")

    def save_progress(self, friend, taken, *, complete=False):
        if friend is None or self.dry_run:
            return
        previous = self.pending_for(friend)
        if previous is not None:
            self.pending = [item for item in self.pending if item is not previous]
        if not complete:
            self.pending.append({"friend": friend, "taken": set(taken), "checked_at": ""})
        else:
            self.checked = [item for item in self.checked
                            if not Vision.same_friend(friend, item["friend"])]
            self.checked.append({"friend": friend, "taken": set(),
                                 "checked_at": datetime.now(CHINA_TZ).isoformat()})
        for key, items in (("pending_visits", self.pending), ("checked_friends", self.checked)):
            self.limit.data[key] = [
                {"route_version": self.route.get("version"), "name": item["friend"].name,
                 "identity_shape": list(item["friend"].identity.shape),
                 "identity": item["friend"].identity.astype(np.uint8).tobytes().hex(),
                 "taken_plots": sorted(item["taken"]), "checked_at": item["checked_at"]}
                for item in items
            ]
        self.limit.observe()

    def guard(self):
        blocked_until = take_blocked_until()
        if blocked_until is not None and not self.dry_run:
            self.report["blocked_until"] = blocked_until.isoformat()
            raise StopVisit(f"游戏夜间禁止拿取（00:00–08:00），{blocked_until:%Y-%m-%d %H:%M} 后恢复")
        if self.host.datetime.now() >= self.wake_time-timedelta(seconds=15):
            raise StopVisit("已接近下次自家务农时间")
        if time.monotonic() >= self.deadline:
            raise StopVisit("本轮好友拿取时间预算已用完")
        self.limit.refresh()
        if self.limit.exhausted and not self.dry_run:
            raise StopVisit("今日拿取次数已用完，08:00 后恢复")

    def capture(self, label="friend"):
        self.guard()
        self.host.screenshot(self.host.SCREENSHOT_PATH, archive_label=label)
        image = cv2.imread(self.host.SCREENSHOT_PATH)
        if image is None or (image.shape[1], image.shape[0]) != SIZE:
            raise StopVisit("偷菜画面不是已校准的 1280x720")
        return image

    def tap(self, x, y):
        self.guard()
        result = self.host.adb_shell_result(f"input tap {int(x)} {int(y)}")
        if result.returncode != 0:
            raise StopVisit("ADB 点击失败")

    def move(self, moves):
        for angle, duration in moves:
            self.guard()
            x, y = self.route["joystick_center"]
            radius = self.route["joystick_radius"]
            tx = round(x + radius*math.cos(math.radians(angle)))
            ty = round(y - radius*math.sin(math.radians(angle)))
            result = self.host.adb_shell_result(f"input swipe {x} {y} {tx} {ty} {duration}")
            if result.returncode != 0:
                raise StopVisit("ADB 走位失败")
        time.sleep(.35)

    def open_social(self):
        image = self.capture()
        if not self.vision.panel_open(image):
            point = self.vision.match(image, "social-open", (1180, 175, 1270, 275), .73)
            if point is None:
                raise StopVisit("未识别社交入口")
            self.tap(*point)
            for _ in range(5):
                time.sleep(1)
                if self.vision.panel_open(self.capture()):
                    break
            else:
                raise StopVisit("社交列表未打开")
        # Switch to the same game-friends tab used in the recording.
        self.tap(770, 32)
        time.sleep(.4)
        if not self.vision.panel_open(self.capture()):
            raise StopVisit("未确认游戏好友列表")
        # Start from the top even if the panel remembers its previous scroll.
        for _ in range(3):
            self.guard()
            result = self.host.adb_shell_result("input swipe 1020 150 1020 590 350")
            if result.returncode != 0:
                raise StopVisit("好友列表滚动失败")
        time.sleep(.5)

    def scan_friends(self):
        self.open_social()
        last = None
        for _ in range(12):
            image = self.capture()
            rows = self.vision.friends(image)
            if not rows:
                time.sleep(.6)
                image = self.capture()
                rows = self.vision.friends(image)
            yield rows
            if not rows or not any(row.pinned for row in rows):
                break  # Pinned entries occupy the top yellow group.
            part = image[85:616, 709:1266]
            if last is not None and np.mean(cv2.absdiff(part, last)) < 2:
                break
            last = part.copy()
            self.guard()
            result = self.host.adb_shell_result("input swipe 1020 554 1020 190 550")
            if result.returncode != 0:
                raise StopVisit("好友列表滚动失败")
            time.sleep(.6)

    def next_friend(self):
        candidates = []
        for rows in self.scan_friends():
            for row in rows:
                if (not row.pinned
                        or any(self.vision.same_friend(row, old) for old in self.visited)
                        or any(self.vision.same_friend(row, old) for old in candidates)):
                    continue
                candidates.append(row)
        print(f"  好友扫描: 待检查置顶 {len(candidates)}，可拿取图标 "
              f"{sum(row.available for row in candidates)}，待补查 "
              f"{sum(self.pending_for(row) is not None for row in candidates)}", flush=True)
        # The ready badge is a scheduling hint: an absent or missed badge must
        # not suppress a pinned account or an interrupted farm's remaining plots.
        candidates.sort(key=lambda row: (self.pending_for(row) is None, not row.available,
                                         self.checked_at(row)))
        if not candidates:
            return None
        for target in candidates:
            for rows in self.scan_friends():
                if not any(row.pinned and self.vision.same_friend(target, row) for row in rows):
                    continue
                fresh = self.vision.friends(self.capture())
                match = next((row for row in fresh if row.pinned
                              and self.vision.same_friend(target, row)), None)
                if match is not None:
                    return match
            print(f"  ⚠️ 置顶好友 {target.name or '昵称未识别'} 未能重新定位，检查其他好友", flush=True)
        raise StopVisit("置顶好友重新定位失败，保留未完成进度")

    def visit(self, friend):
        print(f"  拜访置顶好友: {friend.name or '昵称未识别'} "
              f"(可拿取图标={'有' if friend.available else '无'}，"
              f"补查={'是' if self.pending_for(friend) else '否'})", flush=True)
        self.tap(1228, friend.y)
        for _ in range(35):
            time.sleep(1)
            image = self.capture()
            if self.vision.at_friend(image) and not self.vision.panel_open(image):
                time.sleep(1)
                self.visited.append(friend)
                return
        raise StopVisit("拜访加载超时")

    def remember(self, state):
        if self.dry_run:
            return
        if state.feedback == "night_closed":
            raise StopVisit("游戏提示夜间禁止拿取（00:00–08:00），保留每日剩余次数")
        if state.remaining == 0 or state.feedback == "exhausted":
            # Require a second new screenshot before persisting the day's stop.
            confirmed = self.vision.plot(self.capture())
            if confirmed.remaining == 0 or confirmed.feedback == "exhausted":
                self.limit.observe(0, exhausted=True)
                return  # Let the final successful take be recorded, then guard stops navigation.
            raise StopVisit("次数上限提示未能复核，本轮停止")
        if state.remaining is not None:
            self.limit.observe(state.remaining)

    @staticmethod
    def unknown_plot(plot_id, state):
        print(f"  ⚠️ 好友地块 {plot_id} 识别详情: 选中={state.selected}，"
              f"按钮={state.button}，禁用={state.disabled}，剩余次数={state.remaining}，"
              f"库存={state.stock}，反馈={state.feedback}，OCR={state.text[:240]}", flush=True)
        return "unknown"

    def take_plot(self, plot_id):
        """Only return resolved outcomes; the route handles uncertain stops."""
        before = self.vision.plot(self.capture(f"friend_{plot_id}_before"))
        self.remember(before)
        self.guard()
        if not before.selected:
            time.sleep(.6)
            before = self.vision.plot(self.capture())
            self.remember(before)
            self.guard()
        if not before.selected:
            return self.unknown_plot(plot_id, before)
        if self.dry_run:
            return "observed"
        # A previous plot's toast survives movement (recording frame 18).
        # Only this plot's crop panel can resolve an unripe stop before a tap.
        if before.unripe:
            return "unavailable"
        if before.button is None:
            time.sleep(.7)
            stable = self.vision.plot(self.capture())
            self.remember(stable)
            self.guard()
            if stable.selected and stable.unripe:
                return "unavailable"
            if stable.selected and stable.disabled and before.disabled:
                return "unavailable"
            before = stable
        for _ in range(2):
            if before.button is None:
                return self.unknown_plot(plot_id, before)
            self.tap(*before.button)
            for _ in range(3):
                time.sleep(.45)
                after = self.vision.plot(self.capture(f"friend_{plot_id}_after"))
                self.remember(after)
                if before.remaining is not None and after.remaining is not None and after.remaining < before.remaining:
                    return "taken"
                if (before.stock is not None and after.stock is not None and after.stock < before.stock
                        and after.selected and after.disabled):
                    return "taken"
                if after.feedback in {"taken", "unavailable"} and after.feedback != before.feedback:
                    return after.feedback
                if after.selected and after.disabled:
                    return "unavailable"
            before = after
        return self.unknown_plot(plot_id, before)

    def walk(self, friend=None):
        visit = {"friend": friend.name if friend else "", "plots": [], "complete": False}
        self.report["visits"].append(visit)
        pending = self.pending_for(friend) if friend is not None else None
        taken = set(pending["taken"]) if pending else set()
        self.save_progress(friend, taken)
        self.move(self.route["entry"])
        consecutive_unknown = 0
        for stop in self.route["stops"]:
            self.move(stop["moves"])
            outcome = "already_taken" if stop["id"] in taken else self.take_plot(stop["id"])
            if outcome == "unknown":
                print(f"  ⚠️ 好友地块 {stop['id']} 结果未确认，原地复查", flush=True)
                time.sleep(.8)
                outcome = self.take_plot(stop["id"])
            visit["plots"].append({"id": stop["id"], "outcome": outcome})
            print(f"  好友地块 {stop['id']}: {outcome}", flush=True)
            if outcome == "taken":
                taken.add(stop["id"])
            self.save_progress(friend, taken)
            consecutive_unknown = consecutive_unknown + 1 if outcome == "unknown" else 0
            if consecutive_unknown >= 3:
                print("  ⚠️ 连续三块结果未确认，保留进度并切换下一好友", flush=True)
                return
        visit["complete"] = all(plot["outcome"] != "unknown" for plot in visit["plots"])
        self.save_progress(friend, taken, complete=visit["complete"])
        if not visit["complete"]:
            print("  ⚠️ 本好友仍有未确认地块，下轮优先补查", flush=True)

    def home(self):
        image = self.capture()
        if self.vision.panel_open(image):
            self.tap(672, 358)
            time.sleep(.4)
            image = self.capture()
        if not self.vision.at_friend(image):
            return
        self.tap(1160, 39)
        for _ in range(35):
            time.sleep(1)
            image = self.capture()
            if (not self.vision.at_friend(image)
                    and self.vision.match(image, "social-open", (1180, 175, 1270, 275), .73)):
                return
        raise StopVisit("回家加载超时")

    def run(self):
        try:
            self.guard()
            self.home()
            for _ in range(12):
                friend = self.next_friend()
                if friend is None:
                    self.report["status"] = "置顶好友已全部检查"
                    break
                self.visit(friend)
                self.walk(friend)
                self.home()
            else:
                self.report["status"] = "本轮好友数量保护上限"
        except StopVisit as exc:
            self.report["status"] = str(exc)
        except Exception as exc:
            self.report["status"] = f"偷菜失败: {type(exc).__name__}: {exc}"
        finally:
            # Main's finally exits the game even if returning home cannot finish.
            self.report["finished"] = datetime.now(CHINA_TZ).isoformat()
            plots = [plot for visit in self.report["visits"] for plot in visit["plots"]]
            self.report["outcomes"] = {outcome: sum(p["outcome"] == outcome for p in plots)
                                       for outcome in ("taken", "unavailable", "unknown", "observed", "already_taken")}
            self.report["pending_friends"] = [item["friend"].name for item in self.pending]
            incomplete = [visit for visit in self.report["visits"] if not visit["complete"]]
            if (incomplete or self.pending) and self.report["status"] == "置顶好友已全部检查":
                self.report["status"] = "置顶好友已遍历，未确认地块留待下轮补查"
            atomic_json(self.host.DATA_DIR / "friend_visits_last_run.json", self.report)
            print(f"  好友拿取结束: {self.report['status']}", flush=True)
        return self.report


def run(host, wake_time):
    if (wake_time-host.datetime.now()).total_seconds() < 60:
        print("  距下次务农不足一分钟，跳过好友拿取")
        return None
    return Runner(host, wake_time).run()
