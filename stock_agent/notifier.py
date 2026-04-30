from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AgentConfig, PushConfig


@dataclass
class PushResult:
    sent: bool
    provider: str
    message: str


def notify_report(
    config: AgentConfig,
    title: str,
    report_path: str,
    summary: Optional[str] = None,
) -> PushResult:
    path = Path(report_path)
    content = summary if summary is not None else path.read_text(encoding="utf-8")
    content = append_report_path(content, path)
    return send_markdown(config.push, title, content)


def send_markdown(push_config: PushConfig, title: str, content: str) -> PushResult:
    if not push_config.enabled or push_config.provider == "disabled":
        return PushResult(False, push_config.provider, "push disabled")

    body = trim_markdown(content, push_config.max_chars)
    provider = push_config.provider.lower()
    if provider in {"wecom", "wechat", "wechat_work", "enterprise_wechat"}:
        if not push_config.wechat_webhook_url:
            return PushResult(False, provider, "missing wechat_webhook_url")
        return send_wecom_markdown(push_config.wechat_webhook_url, title, body)
    if provider in {"serverchan", "server_chan", "server酱"}:
        if not push_config.server_chan_send_key:
            return PushResult(False, provider, "missing server_chan_send_key")
        return send_server_chan(push_config.server_chan_send_key, title, body)
    if provider in {"pushplus", "push_plus"}:
        if not push_config.pushplus_token:
            return PushResult(False, provider, "missing pushplus_token")
        return send_pushplus(push_config.pushplus_token, title, body)
    if provider == "console":
        print(f"[Stock Agent Push] {title}\n{body}")
        return PushResult(True, provider, "printed to console")
    return PushResult(False, provider, f"unsupported provider: {provider}")


def send_wecom_markdown(webhook_url: str, title: str, body: str) -> PushResult:
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": f"### {title}\n{body}"},
    }
    return post_json(webhook_url, payload, "wecom")


def send_server_chan(send_key: str, title: str, body: str) -> PushResult:
    url = f"https://sctapi.ftqq.com/{send_key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": body}).encode("utf-8")
    return post_form(url, data, "serverchan")


def send_pushplus(token: str, title: str, body: str) -> PushResult:
    payload = {
        "token": token,
        "title": title,
        "content": body,
        "template": "markdown",
    }
    return post_json("https://www.pushplus.plus/send", payload, "pushplus")


def post_json(url: str, payload: dict, provider: str) -> PushResult:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    return send_request(request, provider)


def post_form(url: str, data: bytes, provider: str) -> PushResult:
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    return send_request(request, provider)


def send_request(request: urllib.request.Request, provider: str) -> PushResult:
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read().decode("utf-8", errors="replace")
            if 200 <= response.status < 300:
                return PushResult(True, provider, text[:200])
            return PushResult(False, provider, f"HTTP {response.status}: {text[:200]}")
    except Exception as exc:
        return PushResult(False, provider, str(exc))


def trim_markdown(content: str, max_chars: int) -> str:
    max_chars = max(800, max_chars)
    if len(content) <= max_chars:
        return content
    return content[: max_chars - 80].rstrip() + "\n\n...内容过长已截断，请打开本地完整报告查看。"


def append_report_path(content: str, path: Path) -> str:
    absolute = path.resolve()
    suffix = f"\n\n完整报告：`{absolute}`"
    if str(absolute) in content:
        return content
    return content.rstrip() + suffix
