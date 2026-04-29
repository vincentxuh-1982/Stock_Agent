from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import (
    AnalysisResult,
    Hotspot,
    MarketSessionStatus,
    Position,
    RealtimeResult,
    StockCandidate,
)
from .prediction import forecast_condition_text, forecast_probability_text
from .recommender import entry_advice, position_advice, summarize_market


def write_market_review(
    output_dir: str,
    index_results: List[AnalysisResult],
    watch_results: List[AnalysisResult],
    errors: Optional[List[str]] = None,
) -> Path:
    return write_report_file(
        output_dir,
        "market_review",
        render_market_review(index_results, watch_results, errors or []),
        archive=True,
    )


def write_news_report(
    output_dir: str,
    hotspots: List[Hotspot],
    candidates: Optional[List[StockCandidate]] = None,
) -> Path:
    return write_report_file(
        output_dir,
        "news_hotspots",
        render_news_report(hotspots, candidates or []),
        archive=True,
    )


def write_portfolio_report(
    output_dir: str,
    results: List[AnalysisResult],
    positions: List[Position],
    errors: Optional[List[str]] = None,
) -> Path:
    return write_report_file(
        output_dir,
        "portfolio_advice",
        render_portfolio_report(results, positions, errors or []),
        archive=True,
    )


def write_realtime_report(
    output_dir: str,
    results: List[RealtimeResult],
    inactive: Optional[List[MarketSessionStatus]] = None,
    errors: Optional[List[str]] = None,
) -> Path:
    return write_report_file(
        output_dir,
        "realtime_watchlist",
        render_realtime_report(results, inactive or [], errors or []),
        archive=False,
    )


def write_report_file(
    output_dir: str,
    prefix: str,
    content: str,
    archive: bool = False,
) -> Path:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    current_path = target_dir / f"{prefix}_latest.md"
    current_path.write_text(content, encoding="utf-8")
    if archive:
        history_dir = target_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / f"{prefix}_{timestamp()}.md"
        history_path.write_text(content, encoding="utf-8")
    return current_path


def render_market_review(
    index_results: List[AnalysisResult],
    watch_results: List[AnalysisResult],
    errors: Optional[List[str]] = None,
) -> str:
    lines = [
        "# 每日市场复盘",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 市场结论：{summarize_market(index_results)}",
        "",
        "## 指数",
        "",
    ]
    lines.extend(render_result_table(index_results, include_advice=False))
    lines.extend(["", "## 自选股", ""])
    lines.extend(render_result_table(watch_results, include_advice=True))
    lines.extend(render_errors(errors or []))
    lines.extend(["", "## 风险提示", ""])
    lines.append("概率预测来自本地量价规则模型，表示当前条件下的倾向，不是确定性预测，也不构成投资建议。")
    return "\n".join(lines) + "\n"


def render_realtime_report(
    results: List[RealtimeResult],
    inactive: Optional[List[MarketSessionStatus]] = None,
    errors: Optional[List[str]] = None,
) -> str:
    inactive = inactive or []
    total_count = len(results) + len(inactive)
    lines = [
        "# 实时行情分析",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 覆盖标的：{total_count}",
        f"- 交易中标的：{len(results)}",
        f"- 未在交易时间段：{len(inactive)}",
        "",
        "## 盘中优先关注",
        "",
    ]
    focus = [item for item in results if item.urgency >= 3]
    if focus:
        lines.extend(render_realtime_table(focus))
    else:
        lines.append("当前没有处于交易时段且高优先级的盘中异动。")

    lines.extend(["", "## 交易中股票/指数", ""])
    if results:
        lines.extend(render_realtime_table(results))
    else:
        lines.append("当前没有处于交易时段的股票或指数。")

    lines.extend(["", "## 未在交易时间段", ""])
    if inactive:
        lines.extend(render_inactive_table(inactive))
    else:
        lines.append("全部覆盖标的当前都在交易时段。")
    lines.extend(render_errors(errors or []))
    lines.extend(["", "## 使用边界", ""])
    lines.append("只有处于当日开市到收市之间的股票或指数才会拉取实时快照并生成当日概率预测；午休仍按实时段处理，未开盘、盘后、周末/节假日标的只显示交易状态。正式交易决策仍需结合收盘确认、仓位和风险预算。")
    return "\n".join(lines) + "\n"


def render_news_report(
    hotspots: List[Hotspot],
    candidates: Optional[List[StockCandidate]] = None,
) -> str:
    lines = [
        "# 新闻热点扫描",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if not hotspots:
        lines.extend(
            [
                "未识别到热点。请在配置文件中添加可访问的 RSS/Atom 新闻源，或检查网络连接。",
                "",
            ]
        )
        return "\n".join(lines)

    for hotspot in hotspots:
        related = ", ".join(hotspot.related_symbols) or "待配置"
        lines.extend(
            [
                f"## {hotspot.theme}",
                "",
                f"- 热度分：{hotspot.score}",
                f"- 自选股映射：{related}",
                "- 代表新闻：",
            ]
        )
        if hotspot.headlines:
            for item in hotspot.headlines:
                link = f" [{item.source}]({item.link})" if item.link else f" {item.source}"
                lines.append(f"  - {item.title}{link}")
        else:
            lines.append("  - 暂无新闻标题，主要来自人气榜/热词。")
        if hotspot.candidates:
            lines.extend(["", "- 推荐观察池："])
            for candidate in hotspot.candidates:
                lines.append(
                    f"  - {candidate_label(candidate)}（{candidate.market}，人气排名 {candidate.score}，涨跌 {candidate.change_pct:.2f}%）：{candidate.reason}"
                )
        lines.append("")
    market_candidates = dedupe_report_candidates(candidates or [])
    if market_candidates:
        lines.extend(["## 市场人气榜", ""])
        for candidate in market_candidates[:16]:
            lines.append(
                f"- {candidate_label(candidate)}（{candidate.market}，排名 {candidate.score}，涨跌 {candidate.change_pct:.2f}%）：{candidate.reason}"
            )
        lines.append("")
    lines.append("热点和推荐观察池仅代表信息密度与市场关注度，不等于投资价值，需要结合估值、业绩和技术结构二次筛选。")
    return "\n".join(lines) + "\n"


def dedupe_report_candidates(candidates: List[StockCandidate]) -> List[StockCandidate]:
    output: List[StockCandidate] = []
    seen = set()
    for candidate in candidates:
        key = f"{candidate.market}:{candidate.symbol}:{candidate.name}"
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return sorted(output, key=lambda item: (item.market, item.score))


def candidate_label(candidate: StockCandidate) -> str:
    if not candidate.symbol or candidate.symbol == candidate.name:
        return candidate.name
    return f"{candidate.symbol} {candidate.name}"


def render_realtime_table(results: List[RealtimeResult]) -> List[str]:
    lines = [
        "| 代码 | 名称 | 市场 | 现价 | 涨跌 | 状态 | 当日概率 | 优先级 | 量能 | 位置 | 条件说明 | 动作 |",
        "| --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for result in results:
        quote = result.quote
        position = f"{result.support:.2f}-{result.resistance:.2f}"
        forecast = result.intraday_forecast
        probability = forecast_probability_text(forecast) if forecast else "-"
        condition = forecast_condition_text(forecast) if forecast else "-"
        lines.append(
            "| {symbol} | {name} | {market} | {price:.2f} | {change:.2%} | {status} | {probability} | {urgency} | {amount_ratio:.0%} | {position} | {condition} | {action} |".format(
                symbol=result.instrument.symbol,
                name=result.instrument.name,
                market=result.session.market if result.session else result.instrument.market,
                price=quote.price,
                change=quote.change_pct,
                status=result.status,
                probability=probability,
                urgency=result.urgency,
                amount_ratio=result.amount_ratio,
                position=position,
                condition=condition,
                action=result.action,
            )
        )
    return lines


def render_inactive_table(inactive: List[MarketSessionStatus]) -> List[str]:
    lines = [
        "| 代码 | 名称 | 市场 | 状态 | 当前阶段 | 交易时段 | 下次可交易 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for status in inactive:
        lines.append(
            "| {symbol} | {name} | {market} | 未在交易时间段 | {phase} | {session_text} | {next_open} |".format(
                symbol=status.instrument.symbol,
                name=status.instrument.name,
                market=status.market,
                phase=status.phase,
                session_text=status.session_text,
                next_open=status.next_open or "-",
            )
        )
    return lines


def render_portfolio_report(
    results: List[AnalysisResult],
    positions: List[Position],
    errors: Optional[List[str]] = None,
) -> str:
    by_symbol: Dict[str, AnalysisResult] = {
        result.instrument.symbol: result for result in results
    }
    lines = [
        "# 持仓操作建议",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| 代码 | 名称 | 成本 | 现价 | 盈亏 | 短期 | 未来3日概率 | 条件说明 | 中期 | 风险 | 建议 |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    active_positions = [position for position in positions if position.shares > 0]
    if not active_positions:
        lines.append("| - | - | - | - | - | - | - | - | - | - | 暂无有效持仓 |")
    for position in active_positions:
        result = by_symbol.get(position.symbol)
        if result is None:
            lines.append(
                f"| {position.symbol} | {position.name} | {position.cost:.2f} | - | - | - | - | - | - | - | 未获取到行情 |"
            )
            continue
        pnl_pct = (
            (result.close - position.cost) / position.cost if position.cost else 0.0
        )
        forecast = result.forecast_3d
        probability = forecast_probability_text(forecast) if forecast else "-"
        condition = forecast_condition_text(forecast) if forecast else "-"
        lines.append(
            "| {symbol} | {name} | {cost:.2f} | {close:.2f} | {pnl:.1%} | {short} | {probability} | {condition} | {mid} | {risk} | {advice} |".format(
                symbol=position.symbol,
                name=position.name,
                cost=position.cost,
                close=result.close,
                pnl=pnl_pct,
                short=result.short_view,
                probability=probability,
                condition=condition,
                mid=result.mid_view,
                risk=result.risk_level,
                advice=position_advice(result, position),
            )
        )
    lines.extend(render_errors(errors or []))
    lines.extend(["", "本报告仅用于研究复盘，不构成投资建议。"])
    return "\n".join(lines) + "\n"


def render_result_table(
    results: List[AnalysisResult],
    include_advice: bool,
) -> List[str]:
    if include_advice:
        lines = [
            "| 代码 | 名称 | 收盘 | 涨跌 | 短期 T+1~3 | 未来3日概率 | 条件说明 | 中期 T+30~60 | 风险 | 关键信号 | 建仓建议 |",
            "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    else:
        lines = [
            "| 代码 | 名称 | 收盘 | 涨跌 | 短期 T+1~3 | 未来3日概率 | 条件说明 | 中期 T+30~60 | 风险 | 关键信号 |",
            "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
        ]

    for result in results:
        signal = "；".join(result.signals[:3])
        forecast = result.forecast_3d
        probability = forecast_probability_text(forecast) if forecast else "-"
        condition = forecast_condition_text(forecast) if forecast else "-"
        if include_advice:
            base = (
                f"| {result.instrument.symbol} | {result.instrument.name} | {result.close:.2f} | "
                f"{result.change_pct:.2%} | {result.short_view} | {probability} | {condition} | "
                f"{result.mid_view} | {result.risk_level} | {signal} | {entry_advice(result)} |"
            )
        else:
            base = (
                f"| {result.instrument.symbol} | {result.instrument.name} | {result.close:.2f} | "
                f"{result.change_pct:.2%} | {result.short_view} | {probability} | {condition} | "
                f"{result.mid_view} | {result.risk_level} | {signal} |"
            )
        lines.append(base)
    return lines


def render_errors(errors: List[str]) -> List[str]:
    if not errors:
        return []
    lines = ["", "## 数据异常", ""]
    for error in errors:
        lines.append(f"- {error}")
    return lines


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
