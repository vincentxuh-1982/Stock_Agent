from __future__ import annotations

import json
import re
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
    return send_markdown(config.push, title, format_mobile_markdown(content))


def send_markdown(push_config: PushConfig, title: str, content: str) -> PushResult:
    if not push_config.enabled or push_config.provider == "disabled":
        return PushResult(False, push_config.provider, "push disabled")

    provider = push_config.provider.lower()
    chunks = split_markdown(content, push_config.max_chars)
    sent_messages = []
    for index, body in enumerate(chunks, start=1):
        chunk_title = title if len(chunks) == 1 else f"{title}（{index}/{len(chunks)}）"
        result = send_markdown_chunk(push_config, provider, chunk_title, body)
        if not result.sent:
            return PushResult(
                False,
                provider,
                f"part {index}/{len(chunks)} failed: {result.message}",
            )
        sent_messages.append(result.message)
    return PushResult(True, provider, f"sent {len(chunks)} message(s); {sent_messages[-1]}")


def send_markdown_chunk(
    push_config: PushConfig,
    provider: str,
    title: str,
    body: str,
) -> PushResult:
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
    return content[: max_chars - 80].rstrip() + "\n\n...内容过长已截断，本地应用保留完整历史。"


def split_markdown(content: str, max_chars: int) -> list[str]:
    max_chars = max(800, max_chars)
    if len(content) <= max_chars:
        return [content]
    chunks: list[str] = []
    current = ""
    for line in content.splitlines(keepends=True):
        if len(line) > max_chars:
            if current:
                chunks.append(current.rstrip())
                current = ""
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars].rstrip())
            continue
        if current and len(current) + len(line) > max_chars:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())
    return [chunk for chunk in chunks if chunk]


def format_mobile_markdown(content: str) -> str:
    lines = content.splitlines()
    output = []
    index = 0
    while index < len(lines):
        line = strip_markdown_links(lines[index]).rstrip()
        if is_table_header(lines, index):
            headers = split_table_row(line)
            rows = []
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(split_table_row(strip_markdown_links(lines[index])))
                index += 1
            output.extend(format_table_for_mobile(headers, rows))
            continue
        if looks_like_local_report_path(line):
            index += 1
            continue
        output.append(line)
        index += 1
    return "\n".join(output).strip() + "\n"


def is_table_header(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return lines[index].lstrip().startswith("|") and is_table_separator(lines[index + 1])


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def format_table_for_mobile(headers: list[str], rows: list[list[str]]) -> list[str]:
    output: list[str] = []
    for row in rows:
        cells = normalize_row(headers, row)
        values = dict(zip(headers, cells))
        code = values.get("代码", "")
        name = values.get("名称", "")
        title = " ".join(part for part in [code, name] if part)
        details = [
            f"{header} {value}"
            for header, value in zip(headers, cells)
            if value and header not in {"代码", "名称"}
        ]
        if title:
            output.append(f"- **{title}**")
            if details:
                output.append(f"  {'；'.join(details)}")
        elif details:
            output.append(f"- {'；'.join(details)}")
    return output


def normalize_row(headers: list[str], row: list[str]) -> list[str]:
    if len(row) >= len(headers):
        return row[: len(headers)]
    return row + [""] * (len(headers) - len(row))


def strip_markdown_links(line: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)


def looks_like_local_report_path(line: str) -> bool:
    return bool(re.search(r"(完整报告|报告路径).*/(reports|StockAgent)/", line))
