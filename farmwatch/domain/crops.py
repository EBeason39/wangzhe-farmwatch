"""王者农场助手：crops。所有可变运行状态归 FarmApp 实例所有。"""

from __future__ import annotations
import os
import json
import math
from pathlib import Path
from datetime import timedelta


class CropScheduling:
    def cycle_value_for_log(self, cycle_min):
        """安全描述不可信周期值，避免超长整数格式化失败。"""
        try:
            description = str(cycle_min)
        except Exception:
            return f"<{type(cycle_min).__name__}>"
        if len(description) > 64:
            return f"<{type(cycle_min).__name__}>"
        return description

    def _strip_crop_variant(self, normalized):
        """Map explicit mutant labels to their base crop (变异柠檬 -> 柠檬)."""
        if normalized.startswith(self.CROP_VARIANT_PREFIX):
            return normalized[len(self.CROP_VARIANT_PREFIX):]
        for suffix in self.CROP_VARIANT_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                return normalized[:-len(suffix)]
        return normalized

    def normalize_crop_name(self, name):
        """Normalize OCR crop names; mutant crops use the same base-cycle record."""
        if not isinstance(name, str):
            return None
        normalized = "".join(name.split()).strip("：:，,。.")
        normalized = self._strip_crop_variant(normalized)
        return normalized or None

    def _crop_name_is_unknown(self, name):
        normalized = self.normalize_crop_name(name)
        return normalized in self.UNKNOWN_CROP_NAMES

    def normalize_supported_cycle(self, cycle_min):
        """Return one of the four supported original crop cycles or ``None``."""
        normalized = self.normalize_stored_cycle(cycle_min)
        if normalized is None:
            return None
        if isinstance(normalized, float) and normalized.is_integer():
            normalized = int(normalized)
        return normalized if normalized in self.SUPPORTED_CROP_CYCLES_MIN else None

    def _format_record_datetime(self, value):
        if isinstance(value, self._DATETIME_TYPE):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _normalize_cycle_counts(self, raw):
        """Only validated first-watering observations are votes; legacy maxima are not."""
        if not isinstance(raw, dict):
            return {}
        counts = {}
        for cycle in self.SUPPORTED_CROP_CYCLES_MIN:
            count = raw.get(str(cycle))
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                counts[str(cycle)] = count
        return counts

    def _most_frequent_cycle(self, counts, preferred=None):
        if not counts:
            return preferred
        frequency = max(counts.values())
        tied = sorted(int(cycle) for cycle, count in counts.items() if count == frequency)
        # Stable ties: keep the prior mode; otherwise choose the smallest cycle.
        return preferred if preferred in tied else tied[0]

    def _normalize_crop_record_entry(self, crop_name, raw):
        """Validate one crop entry and expose the backward-compatible flat view."""
        if not isinstance(raw, dict):
            return None
        crop_name = self.normalize_crop_name(crop_name or raw.get("crop_name"))
        cycle_counts = self._normalize_cycle_counts(raw.get("cycle_counts"))
        cycle_min = self._most_frequent_cycle(
            cycle_counts, self.normalize_supported_cycle(raw.get("cycle_min"))
        )
        if crop_name is None or cycle_min is None:
            return None

        initial_remaining = raw.get("initial_remaining_min")
        if (
            isinstance(initial_remaining, bool)
            or not isinstance(initial_remaining, (int, float))
            or not math.isfinite(float(initial_remaining))
            or initial_remaining <= 0
        ):
            initial_remaining = cycle_min * 11.0 / 12.0

        last_remaining = raw.get("last_remaining_min")
        if (
            isinstance(last_remaining, bool)
            or not isinstance(last_remaining, (int, float))
            or not math.isfinite(float(last_remaining))
            or last_remaining < 0
        ):
            last_remaining = None

        return {
            "schema_version": self.CROP_RECORD_SCHEMA_VERSION,
            "crop_name": crop_name,
            "cycle_min": cycle_min,
            "cycle_counts": cycle_counts,
            "last_cycle_sample_at": raw.get("last_cycle_sample_at"),
            "initial_remaining_min": float(initial_remaining),
            "last_remaining_min": (
                float(last_remaining) if last_remaining is not None else None
            ),
            "planted_at": raw.get("planted_at") or raw.get("update_time"),
            "updated_at": raw.get("updated_at") or raw.get("update_time"),
        }

    def _load_crop_store(self):
        """Load schema 1/2/3 data into a validated schema-3 in-memory store."""
        empty = {
            "schema_version": self.CROP_RECORD_SCHEMA_VERSION,
            "current_crop_name": None,
            "crops": {},
        }
        if not os.path.exists(self.CYCLE_FILE):
            return empty
        try:
            with open(self.CYCLE_FILE, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError, TypeError):
            return empty
        if not isinstance(raw, dict):
            return empty

        raw_schema = raw.get("schema_version", 1)
        if isinstance(raw_schema, bool) or raw_schema not in (1, 2, 3):
            return empty

        crops = {}
        if raw_schema == 3:
            raw_crops = raw.get("crops")
            if not isinstance(raw_crops, dict):
                return empty
            for name, entry in raw_crops.items():
                record = self._normalize_crop_record_entry(name, entry)
                # Old data may contain both 柠檬 and an incorrectly separate
                # 变异柠檬 record. Keep the base record instead of letting a
                # duplicated mutant key overwrite it during migration.
                if (
                    record is not None
                    and record["crop_name"] not in crops
                ):
                    crops[record["crop_name"]] = record
            current_name = self.normalize_crop_name(raw.get("current_crop_name"))
            if current_name not in crops:
                current_name = None
        else:
            record = self._normalize_crop_record_entry(raw.get("crop_name"), raw)
            if record is not None:
                crops[record["crop_name"]] = record
                current_name = record["crop_name"]
            else:
                current_name = None

        return {
            "schema_version": self.CROP_RECORD_SCHEMA_VERSION,
            "current_crop_name": current_name,
            "crops": crops,
        }

    def _write_crop_store(self, store):
        serializable_crops = {}
        for name, record in store["crops"].items():
            serializable_crops[name] = {
                key: value
                for key, value in record.items()
                if key not in ("schema_version", "crop_name")
            }
        data = {
            "schema_version": self.CROP_RECORD_SCHEMA_VERSION,
            "current_crop_name": store.get("current_crop_name"),
            "crops": serializable_crops,
        }
        target = Path(self.CYCLE_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return data

    def _remove_crop_record(self, crop_name):
        store = self._load_crop_store()
        crop_name = self.normalize_crop_name(crop_name)
        if crop_name not in store["crops"]:
            return
        del store["crops"][crop_name]
        if store.get("current_crop_name") == crop_name:
            store["current_crop_name"] = None
        self._write_crop_store(store)

    def save_crop_record(self,
        crop_name,
        cycle_min,
        planted_at=None,
        initial_remaining_min=None,
        last_remaining_min=None,
        cycle_counts=None,
        last_cycle_sample_at=None,
    ):
        """Persist one crop without replacing records belonging to other crops."""
        crop_name = self.normalize_crop_name(crop_name)
        cycle_min = self.normalize_supported_cycle(cycle_min)
        if crop_name is None or cycle_min is None:
            raise ValueError("crop record requires a name and supported cycle")

        store = self._load_crop_store()
        previous = store["crops"].get(crop_name, {})
        counts = self._normalize_cycle_counts(
            previous.get("cycle_counts") if cycle_counts is None else cycle_counts
        )
        cycle_min = self._most_frequent_cycle(counts, cycle_min)
        now_text = self.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if (
            isinstance(initial_remaining_min, bool)
            or not isinstance(initial_remaining_min, (int, float))
            or not math.isfinite(float(initial_remaining_min))
            or initial_remaining_min <= 0
        ):
            initial_remaining_min = cycle_min * 11.0 / 12.0
        record = {
            "schema_version": self.CROP_RECORD_SCHEMA_VERSION,
            "crop_name": crop_name,
            "cycle_min": cycle_min,
            "cycle_counts": counts,
            "last_cycle_sample_at": (last_cycle_sample_at or previous.get("last_cycle_sample_at")),
            "initial_remaining_min": float(initial_remaining_min),
            "last_remaining_min": (
                float(last_remaining_min)
                if isinstance(last_remaining_min, (int, float))
                and not isinstance(last_remaining_min, bool)
                and math.isfinite(float(last_remaining_min))
                else None
            ),
            "planted_at": self._format_record_datetime(planted_at),
            "updated_at": now_text,
        }

        store["crops"][crop_name] = record
        store["current_crop_name"] = crop_name
        self._write_crop_store(store)
        print(
            f"  💾 已保存作物周期: {crop_name} = "
            f"{self.cycle_value_for_log(cycle_min)}分钟"
        )
        return record

    def load_crop_record(self, crop_name=None):
        """Load a named crop, or the current crop when no name is supplied."""
        store = self._load_crop_store()
        requested_name = self.normalize_crop_name(crop_name)
        if crop_name is not None and requested_name is None:
            return None
        selected_name = requested_name or store.get("current_crop_name")
        return store["crops"].get(selected_name)

    def save_crop_cycle(self, crop_name, cycle_min):
        """Backward-compatible wrapper for the old two-field API."""
        return self.save_crop_record(crop_name, cycle_min)

    def load_crop_cycle(self):
        """Backward-compatible tuple view of the persisted crop record."""
        record = self.load_crop_record()
        if record is None:
            return None, None
        print(
            f"  📂 读取存储周期: {record['crop_name']} = "
            f"{self.cycle_value_for_log(record['cycle_min'])}分钟 "
            f"(更新于 {record.get('updated_at') or '未知'})"
        )
        return record["crop_name"], record["cycle_min"]

    def _infer_cycle_from_remaining(self, remaining_min):
        """Infer a fresh cycle from post-first-watering remaining time."""
        candidates = [
            (cycle, cycle * 11.0 / 12.0) for cycle in self.SUPPORTED_CROP_CYCLES_MIN
        ]
        return min(candidates, key=lambda item: abs(item[1] - remaining_min))[0]

    def select_crop_cycle(self, crop_name, remaining_min, now=None, force_new=False):
        """Use the per-crop mode; only the first watering of a new batch adds a vote."""
        if isinstance(remaining_min, bool) or not isinstance(remaining_min, (int, float)):
            raise ValueError("remaining_min must be numeric")
        if not math.isfinite(float(remaining_min)) or remaining_min < 0:
            raise ValueError("remaining_min must be finite and non-negative")
        remaining_min = float(remaining_min)
        normalized_name = self.normalize_crop_name(crop_name)
        if normalized_name is not None:
            record = self.load_crop_record(normalized_name) or self.load_crop_record("作物")
        else:
            record = None if force_new else self.load_crop_record()
        now = now or self.datetime.now()
        recorded_name = record.get("crop_name") if record else None
        upgraded_name = bool(record and normalized_name and self._crop_name_is_unknown(recorded_name))
        selected_name = normalized_name or recorded_name
        counts = self._normalize_cycle_counts(record.get("cycle_counts")) if record else {}
        cycle = record["cycle_min"] if record else self._infer_cycle_from_remaining(remaining_min)
        stamp = self._format_record_datetime(now)
        # force_new is emitted only for a confirmed first watering, never a later OCR retry.
        sampled = bool(force_new and normalized_name and not self._crop_name_is_unknown(normalized_name)
                       and (not record or record.get("last_cycle_sample_at") != stamp))
        if sampled:
            observation = self._infer_cycle_from_remaining(remaining_min)
            key = str(observation)
            counts[key] = counts.get(key, 0) + 1
        cycle = self._most_frequent_cycle(counts, cycle)
        if selected_name is not None:
            saved = self.save_crop_record(
                selected_name, cycle,
                planted_at=now if force_new or not record else record.get("planted_at"),
                initial_remaining_min=(remaining_min if force_new or not record
                                       else record.get("initial_remaining_min")),
                last_remaining_min=remaining_min,
                cycle_counts=counts,
                last_cycle_sample_at=stamp if sampled else (record or {}).get("last_cycle_sample_at"),
            )
            if upgraded_name:
                self._remove_crop_record(recorded_name)
        else:
            saved = record
        return {
            "cycle_min": cycle,
            "crop_name": selected_name,
            "updated": sampled or upgraded_name or (record is None and selected_name is not None),
            "source": ("mode" if sampled else "record-name-upgraded" if upgraded_name
                       else "record-name-unreadable" if record and normalized_name is None
                       else "record" if record else "inferred"),
            "record": saved,
        }

    def clear_crop_cycle(self):
        """End the current batch while retaining learned per-crop cycle data."""
        if not os.path.exists(self.CYCLE_FILE):
            return
        store = self._load_crop_store()
        store["current_crop_name"] = None
        self._write_crop_store(store)
        print("  🧺 已结束当前批次，保留各作物周期记录")

    def normalize_stored_cycle(self, cycle_min):
        """校验持久化周期，返回可计算的数字或 ``None``。"""
        if isinstance(cycle_min, bool) or not isinstance(cycle_min, (int, float)):
            return None
        if isinstance(cycle_min, float) and not math.isfinite(cycle_min):
            return None
        if cycle_min <= 0 or cycle_min > self.MAX_CROP_CYCLE_MIN:
            return None
        if isinstance(cycle_min, float) and cycle_min.is_integer():
            return int(cycle_min)
        return cycle_min

    def _candidate_elapsed_for_watering_count(self,
        remaining_min, watering_count, maintenance_min, cooldown_min
    ):
        """Return the shortest elapsed time for a fixed future-water count.

        ``remaining_min`` is the maturity time immediately after the current
        watering.  The candidate follows the three regions described in the
        optimal-schedule design: cooldown-bound, fully-watered, and dry-tail.
        """
        lower = watering_count * cooldown_min
        upper = watering_count * maintenance_min
        if remaining_min <= 1.25 * lower:
            return lower, False
        if remaining_min <= 1.25 * upper:
            return 0.8 * remaining_min, True
        return remaining_min - upper / 4.0, True

    def _build_watering_intervals(self, total_min, watering_count, maintenance_min, cooldown_min):
        """Build late-as-possible intervals for an already selected candidate."""
        if watering_count <= 0:
            return []

        epsilon = 1e-9
        if total_min > watering_count * maintenance_min + epsilon:
            # At least one interval is necessarily dry.  Keep the earlier
            # intervals at the full moisture duration and put the dry tail last.
            intervals = [maintenance_min] * (watering_count - 1)
            intervals.append(total_min - (watering_count - 1) * maintenance_min)
            return intervals

        intervals = []
        remaining = total_min
        for index in range(watering_count):
            intervals_left = watering_count - index - 1
            reserve_for_rest = intervals_left * cooldown_min
            interval = min(maintenance_min, remaining - reserve_for_rest)
            if interval < cooldown_min - epsilon:
                # This should be unreachable for a valid candidate.  Clamp only
                # tiny floating-point drift; a materially invalid candidate is a
                # programming error rather than a silent schedule change.
                raise ValueError("watering candidate cannot satisfy cooldown")
            intervals.append(interval)
            remaining -= interval

        if abs(remaining) > 1e-7:
            intervals[-1] += remaining
        return intervals

    def plan_optimal_watering_schedule(self,
        start_time,
        remaining_min,
        cycle_min,
        cooldown_min=None,
    ):
        """Plan the earliest harvest with the fewest future waterings.

        ``start_time`` is the completion time of the current watering and
        ``remaining_min`` is the OCR-observed maturity time after that watering.
        The returned ``watering_times`` contains only future actions; the current
        watering is not counted in ``watering_count``.
        """
        if not isinstance(start_time, self._DATETIME_TYPE):
            raise TypeError("start_time must be a datetime")
        if isinstance(cycle_min, bool) or not isinstance(cycle_min, (int, float)):
            raise ValueError("cycle_min must be numeric")
        if cycle_min not in self.SUPPORTED_CROP_CYCLES_MIN:
            raise ValueError(f"unsupported crop cycle: {cycle_min}")
        if isinstance(remaining_min, bool) or not isinstance(remaining_min, (int, float)):
            raise ValueError("remaining_min must be numeric")
        if not math.isfinite(float(remaining_min)) or remaining_min < 0:
            raise ValueError("remaining_min must be finite and non-negative")

        maintenance_min = float(cycle_min) / 3.0
        if cooldown_min is None:
            cooldown_min = float(cycle_min) / 30.0
        if (
            isinstance(cooldown_min, bool)
            or not isinstance(cooldown_min, (int, float))
            or not math.isfinite(float(cooldown_min))
            or cooldown_min <= 0
        ):
            raise ValueError("cooldown_min must be a finite positive number")
        cooldown_min = float(cooldown_min)
        remaining_min = float(remaining_min)

        # Natural maturity is always a valid zero-water fallback.
        best_elapsed = remaining_min
        best_count = 0
        best_exact = False

        for watering_count in range(1, self.MAX_SCHEDULE_WATERINGS + 1):
            lower_bound = watering_count * cooldown_min
            if lower_bound >= best_elapsed - 1e-9:
                break

            elapsed, exact = self._candidate_elapsed_for_watering_count(
                remaining_min,
                watering_count,
                maintenance_min,
                cooldown_min,
            )
            # A plan no earlier than natural maturity is never preferable; it
            # also avoids treating an equal-time watering plan as fewer actions.
            if elapsed >= remaining_min - 1e-9:
                continue
            if elapsed < best_elapsed - 1e-9 or (
                abs(elapsed - best_elapsed) <= 1e-9 and watering_count < best_count
            ):
                best_elapsed = elapsed
                best_count = watering_count
                best_exact = exact

        intervals = self._build_watering_intervals(
            best_elapsed,
            best_count,
            maintenance_min,
            cooldown_min,
        )
        watering_times = []
        accumulated = 0.0
        for interval in intervals:
            accumulated += interval
            watering_times.append(start_time + timedelta(minutes=accumulated))

        mature_time = start_time + timedelta(minutes=best_elapsed)
        next_watering = watering_times[0] if watering_times else None
        return {
            "intervals_min": intervals,
            "watering_times": watering_times,
            "watering_count": best_count,
            "elapsed_min": best_elapsed,
            "mature_time": mature_time,
            "next_watering": next_watering,
            "next_action": "water" if next_watering is not None else "mature",
            "maintenance_min": maintenance_min,
            "cooldown_min": cooldown_min,
            "exact_maturity": best_exact,
        }

    def calculate_plant_cycle_and_water_time(self,
        first_water_time,
        show_mature_time,
        save_if_fresh=False,
        cycle_hint=None,
        crop_name=None,
    ):
        """Calculate and return the current crop's optimal watering plan."""
        time_format = "%H:%M:%S"
        delta_sec = (show_mature_time - first_water_time).total_seconds()
        remain_min = max(0.0, delta_sec / 60.0)
        print(f"  💧 当前浇水后显示剩余时间：{remain_min:.2f} 分钟")

        cycle_min = None
        selected_name = self.normalize_crop_name(crop_name)
        resolved_crop_name = selected_name
        cycle_source = None

        if selected_name is not None:
            selection = self.select_crop_cycle(
                selected_name,
                remain_min,
                now=first_water_time,
                force_new=save_if_fresh,
            )
            cycle_min = selection["cycle_min"]
            resolved_crop_name = selection.get("crop_name") or selected_name
            cycle_source = selection.get("source")
            print(
                f"  🌾 当前作物：{selection.get('crop_name') or selected_name}；"
                f"周期来源：{selection.get('source', 'record')}"
            )
        else:
            # Keep the legacy hint/tuple path for callers and tests that do not
            # yet provide a crop name.  The supported-cycle baseline is now
            # 11*T/12, not the old hard-coded 5-minute rule table.
            if not save_if_fresh:
                usable_cycle_hint = self.normalize_supported_cycle(cycle_hint)
                if cycle_hint is not None and usable_cycle_hint is None:
                    print(
                        f"  ⚠️ 本轮缓存周期格式无效："
                        f"{self.cycle_value_for_log(cycle_hint)}，忽略缓存"
                    )
                if usable_cycle_hint is not None:
                    if remain_min <= usable_cycle_hint * 11.0 / 12.0 + self.OCR_REMAINING_TOLERANCE_MIN:
                        cycle_min = usable_cycle_hint
                        cycle_source = "hint"
                        print(f"  ✅ 使用本轮缓存的作物周期：{cycle_min}分钟")

                if cycle_min is None:
                    stored_crop, stored_cycle = self.load_crop_cycle()
                    usable_stored_cycle = self.normalize_supported_cycle(stored_cycle)
                    if stored_cycle is not None and usable_stored_cycle is None:
                        print(
                            f"  ⚠️ 存储周期格式无效："
                            f"{self.cycle_value_for_log(stored_cycle)}，忽略记录"
                        )
                    if (
                        usable_stored_cycle is not None
                        and remain_min
                        <= usable_stored_cycle * 11.0 / 12.0
                        + self.OCR_REMAINING_TOLERANCE_MIN
                    ):
                        cycle_min = usable_stored_cycle
                        resolved_crop_name = stored_crop
                        cycle_source = "record"
                        print(
                            f"  ✅ 使用存储的作物周期：{stored_crop} = "
                            f"{cycle_min}分钟"
                        )

            if cycle_min is None:
                cycle_min = self._infer_cycle_from_remaining(remain_min)
                if save_if_fresh:
                    # Preserve the legacy generic-name wrapper until OCR supplies
                    # a real crop name in the same round.
                    self.save_crop_cycle("作物", cycle_min)
                    resolved_crop_name = "作物"
                    cycle_source = "inferred"
                    print(f"  💾 新种植，已保存周期：{cycle_min}分钟")
                else:
                    cycle_source = "inferred"
                    print(f"  ⚠️ 无有效周期记录，自动匹配为：{cycle_min}分钟")

        cycle_min = self.normalize_supported_cycle(cycle_min)
        if cycle_min is None:
            cycle_min = self._infer_cycle_from_remaining(remain_min)

        plan = self.plan_optimal_watering_schedule(
            first_water_time,
            remaining_min=remain_min,
            cycle_min=cycle_min,
        )
        cycle_hour = cycle_min // 60
        print(
            f"  🌱 作物原始周期：{cycle_hour} 小时（{cycle_min} 分钟）；"
            f"水分维持 {plan['maintenance_min']:.2f} 分钟；"
            f"冷却 {plan['cooldown_min']:.2f} 分钟"
        )
        print(
            f"  🎯 最短成熟耗时：{plan['elapsed_min']:.3f} 分钟；"
            f"后续浇水 {plan['watering_count']} 次"
        )
        for index, watering_time in enumerate(plan["watering_times"], start=2):
            print(f"  💧 第{index}次完美浇水时间：{watering_time.strftime(time_format)}")
        if not plan["watering_times"]:
            print("  💧 无需后续浇水，等待自然成熟")
        if not plan["watering_times"]:
            print("  ℹ️ 采用自然成熟，无需等待浇水")
        elif plan["exact_maturity"]:
            print("  ✅ 最后一次浇水后正好成熟")
        else:
            print("  ⚠️ 受冷却限制，冷却结束时成熟")
        print(f"  🌾 预计成熟/收获时间：{plan['mature_time'].strftime(time_format)}")

        watering_times = plan["watering_times"]
        return {
            "plant_cycle_min": cycle_min,
            "crop_name": resolved_crop_name,
            "crop_name_recognized": selected_name is not None,
            "cycle_source": cycle_source,
            "water_maintenance_min": plan["maintenance_min"],
            "water_cooldown_min": plan["cooldown_min"],
            "watering_intervals_min": plan["intervals_min"],
            "elapsed_min": plan["elapsed_min"],
            "watering_times": watering_times,
            "watering_count": plan["watering_count"],
            "total_watering_count": plan["watering_count"] + 1,
            "next_watering": plan["next_watering"],
            "next_action": plan["next_action"],
            "exact_maturity": plan["exact_maturity"],
            "water2": watering_times[0] if len(watering_times) > 0 else None,
            "water3": watering_times[1] if len(watering_times) > 1 else None,
            "water4": watering_times[2] if len(watering_times) > 2 else None,
            "mature_time": plan["mature_time"],
        }
