"""运行结果及统计模型。"""
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

class Stats:
    def __init__(self):
        self.rounds = 0          # 执行轮数
        self.harvests = 0        # 成熟收获次数
        self.total_exp = 0       # 累计获得经验
        self.total_crops = {}    # 累计收获作物 {作物名: 数量}
        self.start_time = datetime.now()
        self.last_detail = ""    # 最近一次收获的明细文本（用于微信推送）

    def add_harvest(self, exp=0, crops=None):
        """记录一次收获"""
        self.harvests += 1
        if exp > 0:
            self.total_exp += exp
        if crops:
            for name, count in crops.items():
                self.total_crops[name] = self.total_crops.get(name, 0) + count

    def summary(self):
        elapsed = datetime.now() - self.start_time
        hours = elapsed.total_seconds() / 3600
        print("\n" + "=" * 60)
        print("📊 务农统计")
        print("=" * 60)
        print(f"  执行轮数: {self.rounds}")
        print(f"  成熟收获: {self.harvests} 次")
        if self.total_exp > 0:
            print(f"  累计经验: +{self.total_exp}")
        if self.total_crops:
            for name, qty in self.total_crops.items():
                print(f"  {name}: {qty} 个")
        print(f"  运行时长: {hours:.1f} 小时")
        print(f"  开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

class ScreenshotCaptureError(RuntimeError):
    """截图在有限重试后仍失败。"""

class RoundStatus(str, Enum):
    CLICK_SENT = 'click_sent'
    HARVEST_CONFIRMED = 'harvest_confirmed'
    RECOGNITION_FAILED = 'recognition_failed'
    ACTION_FAILED = 'action_failed'


@dataclass
class RoundResult:
    status: RoundStatus
    click_sent: bool = False
    harvest_confirmed: bool = False
    plan: dict | None = None
