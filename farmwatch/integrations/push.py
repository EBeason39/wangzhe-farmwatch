"""WxPusher / Server酱通知，读取统一 Settings，不修改进程环境。"""
import json
import uuid
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

HTTP_TIMEOUT = 8
WXPUSHER_SIMPLE_PUSH_API = "https://wxpusher.zjiecode.com/api/send/message/simple-push"
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"

def _open_url_with_direct_fallback(req, timeout):
    """Open URL; retry without proxy if a local proxy refuses connection.

    终端里残留的 HTTP_PROXY/HTTPS_PROXY 指向 127.0.0.1 时，代理软件换端口
    或关闭会导致推送误报“目标计算机积极拒绝”。WxPusher/Server酱通常可直连，
    这里对本地代理连接失败自动降级直连。
    """
    # urlopen 可能会根据当前代理设置修改 Request 的代理目标；
    # 先准备一个干净的直连 Request，供本地代理连接失败后重试。
    direct_req = urllib.request.Request(
        req.full_url,
        data=req.data,
        headers=dict(req.headers),
        method=req.get_method(),
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        reason_text = str(reason)
        refused = (
            "10061" in reason_text
            or isinstance(reason, ConnectionRefusedError)
            or any(
                marker in reason_text
                for marker in ("127.0.0.1", "localhost", "::1")
            )
        )
        if not refused:
            raise
        print("  [notify] 本地代理不可用，尝试直连")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )
        return opener.open(direct_req, timeout=timeout)


class Notifier:
    def __init__(self, settings):
        self.settings = settings
        self.state_path = settings.root / 'tmp' / 'notify_state.json'

    def _claim_daily_attempt(self):
        limit = self.settings.notify_daily_limit
        if limit == 0:
            return True
        today = datetime.now().strftime('%Y-%m-%d')
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        count = state.get('count', 0) if state.get('date') == today else 0
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            count = 0
        if count >= limit:
            print(f'  [notify] 已达到每日 {limit} 次通知上限')
            return False
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.state_path.with_name(f'.notify-{uuid.uuid4().hex}.tmp')
        try:
            pending.write_text(json.dumps({'date': today, 'count': count + 1}), encoding='utf-8')
            pending.replace(self.state_path)
        finally:
            pending.unlink(missing_ok=True)
        return True

    def send_push(self, title, desp=''):
        config = self.settings
        if not config.notify_enabled:
            return False
        key = config.notify_spt if config.notify_channel == 'wxpusher' else config.notify_sendkey
        if not key:
            print('  [notify] 当前通道没有配置凭据，跳过通知')
            return False
        try:
            if not self._claim_daily_attempt():
                return False
            if config.notify_channel == 'wxpusher':
                payload = json.dumps({'content': desp or title, 'summary': title[:100],
                                      'contentType': 3, 'spt': key}).encode('utf-8')
                url = WXPUSHER_SIMPLE_PUSH_API
                mime = 'application/json; charset=utf-8'
                success_code = 1000
            else:
                payload = urllib.parse.urlencode({'title': title, 'desp': desp or title}).encode('utf-8')
                url = SERVERCHAN_API.format(key=key)
                mime = 'application/x-www-form-urlencoded; charset=utf-8'
                success_code = 0
            request = urllib.request.Request(url, data=payload, headers={'Content-Type': mime}, method='POST')
            with _open_url_with_direct_fallback(request, HTTP_TIMEOUT) as response:
                body = json.loads(response.read().decode('utf-8', 'replace'))
            ok = isinstance(body, dict) and body.get('code') == success_code
            print(f"  [notify] {'成功' if ok else '失败'}：{config.notify_channel}")
            return ok
        except Exception as exc:
            # 不记录请求 URL、响应正文或异常原文，避免 Server酱 URL 中的令牌泄露。
            print(f'  [notify] 推送异常（{type(exc).__name__}），继续务农')
            return False
