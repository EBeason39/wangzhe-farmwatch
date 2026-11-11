"""1280×720 界面契约：语义控件、搜索范围和可审查的识别条件。"""
from dataclasses import dataclass

FRAME_SIZE = (1280, 720)


@dataclass(frozen=True)
class Control:
    region: tuple[float, float, float, float]
    asset: str | None = None
    labels: tuple[str, ...] = ()
    context: tuple[str, ...] = ()


CONTROLS = {
    'session.start': Control((.25, .60, .75, .90), 'session-start.png'),
    'dialog.close': Control((.65, .02, .99, .35), 'dialog-dismiss.png'),
    'farm.enter': Control((.05, .60, .50, .90), 'farm-enter.png'),
    'farm.action': Control((.45, .35, .85, .75), 'farm-action.png'),
    'farm.reset': Control((.87, .82, .98, .99), 'farm-reset.png'),
    'harvest.continue': Control((.25, .45, .80, .98), labels=('继续', '点击继续', '点击屏幕继续'),
                                context=('收获', '获得', '经验', '恭喜')),
    'account.privacy': Control((.35, .45, .85, .95), labels=('同意', '同意并继续'),
                              context=('隐私', '用户协议')),
    # The age-rating badge covers the end of this label on the login screen.
    # Match the QQ glyph in the rightmost button; the iOS QQ and Android WeChat
    # controls are outside this region.
    'account.android': Control((.72, .70, .96, .86), 'account-qq-android.png'),
    'account.authorize': Control((.25, .35, .75, .90), labels=('同意', '同意授权', '同意并继续'),
                                context=('QQ', '授权', '登录', '申请获得')),
}

SOCIAL_ASSETS = (
    'social-collapse.png', 'social-game-tab.png', 'social-home.png',
    'social-open.png', 'social-ready.png', 'social-take-dim.png',
    'social-take-lit.png', 'social-visit.png',
)


def required_assets():
    return tuple(control.asset for control in CONTROLS.values() if control.asset) + SOCIAL_ASSETS
