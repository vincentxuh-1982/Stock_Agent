from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from .config import AgentConfig
from .models import Portfolio

MAX_CONTEXT_CHARS = 18_000


def assistant_status() -> Dict[str, object]:
    provider = os.getenv("STOCK_AGENT_AI_PROVIDER", "local").strip().lower()
    allow_openai = provider == "openai" and bool(os.getenv("OPENAI_API_KEY"))
    if allow_openai:
        return {
            "provider": "openai",
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "external": True,
        }
    return {
        "provider": "local",
        "model": "report-rules",
        "external": False,
    }


def answer_question(
    question: str,
    kind: str,
    report_markdown: str,
    config: AgentConfig,
    portfolio: Optional[Portfolio] = None,
) -> Dict[str, object]:
    question = question.strip()
    if not question:
        raise ValueError("question is required")

    status = assistant_status()
    context = build_context(kind, report_markdown, config, portfolio)
    if status["provider"] == "openai":
        try:
            answer = call_openai(question, kind, context, str(status["model"]))
            return {
                "answer": answer,
                "provider": status["provider"],
                "model": status["model"],
                "external": True,
            }
        except Exception as exc:
            fallback = local_answer(question, kind, report_markdown, config)
            fallback["answer"] = (
                f"外部 AI 调用失败，已切换到本地报告助手。\n\n"
                f"{fallback['answer']}\n\n"
                f"错误摘要：{str(exc)[:180]}"
            )
            fallback["provider"] = "local_fallback"
            fallback["external"] = False
            return fallback

    return local_answer(question, kind, report_markdown, config)


def call_openai(question: str, kind: str, context: str, model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 900,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是股票研究工作台里的中文 AI 助手。只基于提供的报告、配置和持仓上下文回答，"
                    "不要编造实时行情或新闻。可以给出倾向、条件和风险，但不能给确定性买卖指令，"
                    "也不能声称保证收益。回答要清晰、可执行、简洁。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前页面：{kind}\n\n"
                    f"上下文：\n{context}\n\n"
                    f"用户问题：{question}"
                ),
            },
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail[:220]}") from exc

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenAI response missing assistant content") from exc


def build_context(
    kind: str,
    report_markdown: str,
    config: AgentConfig,
    portfolio: Optional[Portfolio],
) -> str:
    watchlist = ", ".join(
        f"{item.symbol} {item.name}" for item in config.watchlist[:40]
    )
    indices = ", ".join(f"{item.symbol} {item.name}" for item in config.indices)
    positions = ""
    if portfolio:
        positions = ", ".join(
            f"{item.symbol} {item.name} 成本 {item.cost:g} 股数 {item.shares:g}"
            for item in portfolio.positions
        )
    report = report_markdown.strip()
    if len(report) > MAX_CONTEXT_CHARS:
        report = report[:MAX_CONTEXT_CHARS] + "\n\n[报告已截断]"
    return "\n".join(
        [
            f"页面类型：{kind}",
            f"指数池：{indices or '-'}",
            f"自选股：{watchlist or '-'}",
            f"持仓：{positions or '-'}",
            "",
            "当前页面报告：",
            report or "暂无当前报告。",
        ]
    )


def local_answer(
    question: str,
    kind: str,
    report_markdown: str,
    config: AgentConfig,
) -> Dict[str, object]:
    terms = extract_terms(question, config)
    relevant = select_relevant_lines(report_markdown, terms, question)
    page_hint = local_page_hint(kind)
    lines = [
        f"我先按当前「{kind_label(kind)}」页面的报告来回答。",
        "",
    ]
    if relevant:
        lines.append("我抓到的相关依据：")
        for item in relevant[:8]:
            lines.append(f"- {item}")
    else:
        lines.append("当前报告里没有直接命中这个问题的标的或关键词。")
        summary = report_summary_lines(report_markdown)
        if summary:
            lines.append("可参考当前报告的摘要：")
            for item in summary[:5]:
                lines.append(f"- {item}")

    lines.extend(["", "我的判断："])
    lines.extend(local_judgement(question, kind, bool(relevant)))
    if page_hint:
        lines.append(f"- {page_hint}")
    lines.extend(
        [
            "- 这条回答来自本地报告助手，未调用外部大模型；如需更强推理，可配置 OpenAI 接口。",
            "- 概率和建议只表示当前条件下的倾向，不构成确定性买卖指令。",
        ]
    )
    return {
        "answer": "\n".join(lines),
        "provider": "local",
        "model": "report-rules",
        "external": False,
    }


def extract_terms(question: str, config: AgentConfig) -> List[str]:
    terms: List[str] = []
    normalized_question = question.lower()
    for instrument in config.indices + config.watchlist:
        candidates = {
            instrument.symbol,
            instrument.name,
            (instrument.provider_symbol or ""),
        }
        for candidate in candidates:
            candidate = candidate.strip()
            if candidate and candidate.lower() in normalized_question:
                terms.append(candidate)
    for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", question):
        if token not in terms:
            terms.append(token)
    return terms[:12]


def select_relevant_lines(
    markdown: str,
    terms: List[str],
    question: str,
) -> List[str]:
    output: List[str] = []
    seen = set()
    keyword_terms = [term.lower() for term in terms if term.strip()]
    if not keyword_terms:
        keyword_terms = [item.lower() for item in re.findall(r"[\w\u4e00-\u9fff]{2,}", question)]

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or is_table_separator(line):
            continue
        lower = line.lower()
        if not any(term in lower for term in keyword_terms):
            continue
        cleaned = clean_markdown_line(line)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
        if len(output) >= 10:
            break
    return output


def report_summary_lines(markdown: str) -> List[str]:
    output: List[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or is_table_separator(line):
            continue
        if line.startswith(("#", "- ")) or line.startswith("|"):
            cleaned = clean_markdown_line(line)
            if cleaned and cleaned not in output:
                output.append(cleaned)
        if len(output) >= 6:
            break
    return output


def clean_markdown_line(line: str) -> str:
    if line.startswith("#"):
        return line.lstrip("#").strip()
    if line.startswith("- "):
        return line[2:].strip()
    if line.startswith("|"):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        cells = [cell for cell in cells if cell and not set(cell) <= {"-", ":"}]
        return " / ".join(cells)
    return line


def is_table_separator(line: str) -> bool:
    stripped = line.replace("|", "").replace("-", "").replace(":", "").strip()
    return not stripped


def local_judgement(question: str, kind: str, has_relevant: bool) -> List[str]:
    text = question.lower()
    output: List[str] = []
    if any(word in text for word in ["买", "卖", "加仓", "减仓", "建仓", "操作", "建议"]):
        output.append("先把报告里的条件说明当作触发器，而不是直接按概率下单。")
        output.append("若没有仓位，优先等价格重新站回关键均线或支撑确认；若已有仓位，先看止损线和仓位上限。")
    elif any(word in text for word in ["热点", "方向", "题材", "机会", "产业"]):
        output.append("优先看新闻/洞察里同时具备新闻密度、候选标的、人气延续的主题。")
        output.append("单日热度不够，最好等 2-3 个交易日仍有资金和新闻共振再提高权重。")
    elif any(word in text for word in ["风险", "跌", "回撤", "止损"]):
        output.append("先检查是否跌破支撑、MA20 或报告里的条件价；这些位置失守时，概率会向下修正。")
        output.append("若只是盘中波动但没有放量破位，适合等收盘确认后再判断。")
    else:
        output.append("可以把当前报告作为第一层过滤，再结合最新公告、成交量和大盘环境二次确认。")
        if has_relevant:
            output.append("相关行已经列在上面，重点看其中的概率、状态、条件说明和动作建议。")
    return [f"- {item}" for item in output]


def local_page_hint(kind: str) -> str:
    hints = {
        "realtime": "实时页只在交易时段做盘中概率；盘后标的会显示为未在交易时间段。",
        "review": "复盘页更适合看未来 3 日和 30-60 日的条件概率。",
        "news": "新闻页更适合判断当下市场关注度，而不是单只股票买卖点。",
        "insights": "洞察页偏两周节奏，适合寻找下一阶段热点候选。",
        "portfolio": "持仓建议页优先服务风险控制和仓位动作。",
    }
    return hints.get(kind, "")


def kind_label(kind: str) -> str:
    labels = {
        "realtime": "实时",
        "review": "复盘",
        "news": "新闻",
        "insights": "洞察",
        "portfolio": "建议",
    }
    return labels.get(kind, kind)
