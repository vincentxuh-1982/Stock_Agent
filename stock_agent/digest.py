from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .analyzer import analyze_many
from .config import AgentConfig
from .data_providers import provider_from_config
from .insights import dedupe_news, news_line, provider_symbol, recent_news
from .models import AnalysisResult, Instrument, NewsItem, Portfolio, Position, RealtimeResult
from .news import fetch_hot_candidates, fetch_news, fetch_stock_news, find_hotspots
from .prediction import forecast_condition_text, forecast_probability_text
from .realtime import is_cn_index, is_hk_index, run_realtime_analysis
from .recommender import entry_advice, position_advice, summarize_market
from .reports import (
    candidate_label,
    render_errors,
    render_realtime_report,
    write_market_review,
    write_news_report,
    write_portfolio_report,
    write_report_file,
)


def run_daily_digest(config: AgentConfig, portfolio: Optional[Portfolio]) -> str:
    provider = provider_from_config(config)
    errors: List[str] = []
    index_results = analyze_many(config.indices, provider, config, errors=errors)
    watch_results = analyze_many(config.watchlist, provider, config, errors=errors)
    active_positions = active_portfolio_positions(portfolio)
    position_instruments = instruments_for_positions(config, active_positions)
    position_results = analyze_many(position_instruments, provider, config, errors=errors)

    news_items = fetch_news(config, limit_per_source=50)
    candidates = fetch_hot_candidates(limit=30, keyword_limit=12)
    hotspots = find_hotspots(config, news_items, candidates)
    focus_news = collect_focus_news(
        unique_instruments(position_instruments + config.watchlist),
        news_items,
    )

    write_market_review(config.output_dir, index_results, watch_results, errors)
    write_news_report(config.output_dir, hotspots, candidates)
    if active_positions:
        write_portfolio_report(config.output_dir, position_results, active_positions, errors)

    content = render_daily_digest(
        config=config,
        index_results=index_results,
        watch_results=watch_results,
        position_results=position_results,
        positions=active_positions,
        hotspots=hotspots,
        candidates=candidates,
        focus_news=focus_news,
        errors=errors,
    )
    path = write_report_file(config.output_dir, "daily_digest", content, archive=True)
    return str(path)


def run_realtime_push_digest(config: AgentConfig, portfolio: Optional[Portfolio]) -> str:
    results, inactive, errors = run_realtime_analysis(config)
    write_report_file(
        config.output_dir,
        "realtime_watchlist",
        render_realtime_report(results, inactive, errors),
        archive=False,
    )
    active_positions = active_portfolio_positions(portfolio)
    content = render_realtime_push_digest(results, active_positions, errors)
    path = write_report_file(config.output_dir, "realtime_push", content, archive=False)
    return str(path)


def render_daily_digest(
    config: AgentConfig,
    index_results: List[AnalysisResult],
    watch_results: List[AnalysisResult],
    position_results: List[AnalysisResult],
    positions: List[Position],
    hotspots,
    candidates,
    focus_news: Dict[str, List[NewsItem]],
    errors: List[str],
) -> str:
    by_position = {position.symbol: position for position in positions}
    by_result = {result.instrument.symbol: result for result in position_results}
    watch_by_symbol = {result.instrument.symbol: result for result in watch_results}
    lines = [
        "# 每日复盘与持仓简报",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 市场结论：{summarize_market(index_results)}",
        f"- 覆盖指数：{len(index_results)}；自选股：{len(watch_results)}；持仓股：{len(positions)}",
        "",
        "## 指数状态",
        "",
    ]
    lines.extend(render_compact_analysis_table(index_results, include_entry=False))

    lines.extend(["", "## 持仓策略", ""])
    if positions:
        lines.extend(render_position_digest_table(positions, by_result))
    else:
        lines.append("当前没有有效持仓。")

    opportunities = select_entry_opportunities(
        [
            result
            for result in watch_results
            if result.instrument.symbol not in by_position
        ]
    )
    lines.extend(["", "## 自选股建仓机会", ""])
    if opportunities:
        lines.extend(render_compact_analysis_table(opportunities, include_entry=True))
    else:
        lines.append("今天自选股里没有达到建仓机会阈值的标的，继续观察为主。")

    lines.extend(["", "## 持仓/自选股新闻与大事", ""])
    focus_order = list(by_position) + [
        result.instrument.symbol
        for result in watch_results
        if result.instrument.symbol not in by_position
    ]
    wrote_news = False
    for symbol in focus_order:
        result = by_result.get(symbol) or watch_by_symbol.get(symbol)
        items = focus_news.get(symbol, [])
        if not result or not items:
            continue
        label = "持仓股" if symbol in by_position else "自选股"
        lines.extend([f"### {symbol} {result.instrument.name}（{label}）", ""])
        for item in items[:4]:
            lines.append(f"- {news_line(item)}")
        lines.append("")
        wrote_news = True
    if not wrote_news:
        lines.append("今天没有抓到明确对应持仓/自选股的个股新闻；以产业新闻和技术结构为主。")

    lines.extend(["", "## 市场热点与行业动态", ""])
    if hotspots:
        for hotspot in hotspots[:6]:
            related = ", ".join(hotspot.related_symbols) or "-"
            lines.append(
                f"- {hotspot.theme}：热度 {hotspot.score}，自选映射 {related}"
            )
            for item in hotspot.headlines[:2]:
                lines.append(f"  - {news_line(item)}")
    else:
        lines.append("未识别到清晰的行业热点。")

    if candidates:
        lines.extend(["", "## 市场人气候选", ""])
        for candidate in candidates[:10]:
            lines.append(
                f"- {candidate_label(candidate)}（{candidate.market}，排名 {candidate.score}，涨跌 {candidate.change_pct:.2f}%）：{candidate.reason}"
            )

    lines.extend(render_errors(errors))
    lines.extend(["", "## 使用边界", ""])
    lines.append("本简报用于收盘后复盘和次日计划整理，概率和策略来自本地量价模型与公开新闻聚合，不构成投资建议。")
    return "\n".join(lines) + "\n"


def render_realtime_push_digest(
    results: List[RealtimeResult],
    positions: List[Position],
    errors: List[str],
) -> str:
    position_by_symbol = {position.symbol: position for position in positions}
    result_by_symbol = {result.instrument.symbol: result for result in results}
    lines = [
        "# 盘中实时策略简报",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 交易中标的：{len(results)}；持仓股：{len(positions)}",
        "",
        "## 持仓股策略",
        "",
    ]
    if positions:
        lines.extend(
            [
                "| 代码 | 名称 | 现价 | 涨跌 | 当日概率 | 趋势/状态 | 策略 |",
                "| --- | --- | ---: | ---: | --- | --- | --- |",
            ]
        )
        for position in positions:
            result = result_by_symbol.get(position.symbol)
            if not result:
                lines.append(
                    f"| {position.symbol} | {position.name} | - | - | - | 未在交易时段/未获取行情 | 暂不动作，等待行情更新 |"
                )
                continue
            quote = result.quote
            probability = forecast_probability_text(result.intraday_forecast)
            lines.append(
                f"| {position.symbol} | {position.name} | {quote.price:.2f} | {quote.change_pct:.2%} | {probability} | {result.status} | {realtime_position_strategy(result, position)} |"
            )
    else:
        lines.append("当前没有有效持仓。")

    opportunities = select_realtime_entry_opportunities(
        [
            result
            for result in results
            if result.instrument.symbol not in position_by_symbol
            and not is_cn_index(result.instrument)
            and not is_hk_index(result.instrument)
        ]
    )
    lines.extend(["", "## 自选股建仓机会", ""])
    if opportunities:
        lines.extend(
            [
                "| 代码 | 名称 | 现价 | 涨跌 | 状态 | 当日概率 | 条件 | 建仓观察 |",
                "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
            ]
        )
        for result in opportunities:
            quote = result.quote
            lines.append(
                f"| {result.instrument.symbol} | {result.instrument.name} | {quote.price:.2f} | {quote.change_pct:.2%} | {result.status} | {forecast_probability_text(result.intraday_forecast)} | {forecast_condition_text(result.intraday_forecast)} | {result.action} |"
            )
    else:
        lines.append("本轮没有达到建仓机会阈值的自选股。")
    lines.extend(render_errors(errors))
    lines.extend(["", "本简报默认每 30 分钟生成一次，仅用于盘中跟踪和风险提醒。"])
    return "\n".join(lines) + "\n"


def render_compact_analysis_table(
    results: Sequence[AnalysisResult],
    include_entry: bool,
) -> List[str]:
    if include_entry:
        lines = [
            "| 代码 | 名称 | 收盘 | 涨跌 | 短期 | 未来3日概率 | 条件 | 建仓策略 |",
            "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
        ]
    else:
        lines = [
            "| 代码 | 名称 | 收盘 | 涨跌 | 短期 | 未来3日概率 | 条件 |",
            "| --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    for result in results:
        probability = forecast_probability_text(result.forecast_3d)
        condition = forecast_condition_text(result.forecast_3d)
        row = (
            f"| {result.instrument.symbol} | {result.instrument.name} | {result.close:.2f} | "
            f"{result.change_pct:.2%} | {result.short_view} | {probability} | {condition} |"
        )
        if include_entry:
            row = row[:-1] + f"| {entry_advice(result)} |"
        lines.append(row)
    return lines


def render_position_digest_table(
    positions: Sequence[Position],
    by_result: Dict[str, AnalysisResult],
) -> List[str]:
    lines = [
        "| 代码 | 名称 | 成本 | 收盘 | 盈亏 | 短期 | 未来3日概率 | 持仓策略 |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for position in positions:
        result = by_result.get(position.symbol)
        if not result:
            lines.append(
                f"| {position.symbol} | {position.name} | {position.cost:.2f} | - | - | - | - | 未获取行情，暂不动作 |"
            )
            continue
        pnl_pct = (result.close - position.cost) / position.cost if position.cost else 0
        lines.append(
            f"| {position.symbol} | {position.name} | {position.cost:.2f} | {result.close:.2f} | {pnl_pct:.1%} | {result.short_view} | {forecast_probability_text(result.forecast_3d)} | {position_advice(result, position)} |"
        )
    return lines


def select_entry_opportunities(results: Sequence[AnalysisResult]) -> List[AnalysisResult]:
    candidates = []
    for result in results:
        up_probability = result.forecast_3d.up_probability if result.forecast_3d else 0.5
        if result.risk_level == "高":
            continue
        if result.score_short >= 2 and result.score_mid >= 1 and up_probability >= 0.52:
            candidates.append(result)
        elif up_probability >= 0.58 and result.score_mid >= 0:
            candidates.append(result)
    return sorted(
        candidates,
        key=lambda item: (
            item.forecast_3d.up_probability if item.forecast_3d else 0.5,
            item.score_short + item.score_mid,
        ),
        reverse=True,
    )[:6]


def select_realtime_entry_opportunities(results: Sequence[RealtimeResult]) -> List[RealtimeResult]:
    candidates = []
    for result in results:
        forecast = result.intraday_forecast
        up_probability = forecast.up_probability if forecast else 0.5
        if up_probability >= 0.56 and result.status in {
            "趋势延续",
            "突破压力",
            "放量上涨",
            "盘中强势",
            "压力试探",
        }:
            candidates.append(result)
        elif result.urgency >= 3 and up_probability >= 0.54 and result.quote.change_pct > 0:
            candidates.append(result)
    return sorted(
        candidates,
        key=lambda item: (
            item.intraday_forecast.up_probability if item.intraday_forecast else 0.5,
            item.urgency,
        ),
        reverse=True,
    )[:5]


def realtime_position_strategy(result: RealtimeResult, position: Position) -> str:
    quote = result.quote
    pnl_pct = (quote.price - position.cost) / position.cost if position.cost else 0.0
    forecast = result.intraday_forecast
    down_probability = forecast.down_probability if forecast else 0.5
    up_probability = forecast.up_probability if forecast else 0.5
    if result.status in {"跌破支撑", "放量回落", "盘中走弱"} and down_probability >= 0.55:
        return (
            f"减仓/控风险优先：现价偏弱，若不能收回 {result.support:.2f}，先降低仓位暴露。"
        )
    if result.status == "支撑告警":
        return f"持有观察：盯 {result.support:.2f}，跌破且放量时减仓。"
    if result.status in {"突破压力", "放量上涨", "趋势延续", "盘中强势"} and up_probability >= 0.55:
        if pnl_pct > 0.1:
            return f"继续持有并上移止盈：盈利 {pnl_pct:.1%}，跌回 {result.support:.2f} 下方减仓。"
        return f"持有，可等回踩 {result.support:.2f} 不破后小幅加仓。"
    if pnl_pct < -position.max_loss_pct:
        return f"亏损超过风控阈值 {position.max_loss_pct:.0%}，优先减仓或止损。"
    return f"持有观察：区间 {result.support:.2f}-{result.resistance:.2f}，等待方向确认。"


def collect_focus_news(
    instruments: Sequence[Instrument],
    market_news: Sequence[NewsItem],
    limit: int = 5,
) -> Dict[str, List[NewsItem]]:
    output: Dict[str, List[NewsItem]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(fetch_stock_news, provider_symbol(instrument), limit): instrument
            for instrument in instruments
        }
        for future in as_completed(futures):
            instrument = futures[future]
            try:
                items = recent_news(dedupe_news(future.result()), days=3)
            except Exception:
                items = []
            if not items:
                items = fallback_news_for_instrument(instrument, market_news, limit)
            output[instrument.symbol] = items[:limit]
    return output


def fallback_news_for_instrument(
    instrument: Instrument,
    market_news: Sequence[NewsItem],
    limit: int,
) -> List[NewsItem]:
    keywords = [instrument.symbol, instrument.name] + list(instrument.themes)
    matches = [
        item
        for item in recent_news(dedupe_news(market_news), days=3)
        if any(keyword and keyword.lower() in f"{item.title} {item.summary}".lower() for keyword in keywords)
    ]
    return matches[:limit]


def instruments_for_positions(
    config: AgentConfig,
    positions: Sequence[Position],
) -> List[Instrument]:
    known = {item.symbol: item for item in config.watchlist + config.indices}
    instruments = []
    for position in positions:
        instrument = known.get(position.symbol)
        if instrument is None:
            instrument = Instrument(
                symbol=position.symbol,
                name=position.name,
                kind=position.kind,
                market=position.market,
                provider_symbol=position.provider_symbol,
            )
        instruments.append(instrument)
    return instruments


def active_portfolio_positions(portfolio: Optional[Portfolio]) -> List[Position]:
    if portfolio is None:
        return []
    return [position for position in portfolio.positions if position.shares > 0]


def unique_instruments(instruments: Iterable[Instrument]) -> List[Instrument]:
    output: List[Instrument] = []
    seen = set()
    for instrument in instruments:
        key = (instrument.market, instrument.kind, instrument.symbol)
        if key in seen:
            continue
        seen.add(key)
        output.append(instrument)
    return output
