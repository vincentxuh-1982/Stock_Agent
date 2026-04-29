from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .config import AgentConfig
from .models import Instrument, NewsItem, StockCandidate
from .news import (
    DEFAULT_THEME_KEYWORDS,
    dedupe_candidates,
    fetch_hot_candidates,
    fetch_news,
    fetch_stock_news,
    normalize,
    themes_for_text,
)
from .reports import candidate_label, write_report_file


INSIGHTS_PREFIX = "biweekly_insights"
INSIGHTS_INTERVAL_DAYS = 14
CANDIDATE_THEME_HINTS = {
    "工业富联": ["算力", "AI", "通信"],
    "拓维信息": ["AI", "算力"],
    "华胜天成": ["AI", "算力"],
    "小米": ["汽车", "AI", "港股"],
    "阿里巴巴": ["AI", "港股"],
    "腾讯": ["AI", "港股"],
    "曦智": ["半导体", "GPU", "AI"],
    "壁仞": ["半导体", "GPU", "AI"],
    "胜宏科技": ["PCB", "算力", "AI"],
    "中国船舶": ["军工"],
    "东方甄选": ["消费", "教育", "港股"],
    "华电": ["新能源"],
}


@dataclass
class ThemeInsight:
    theme: str
    score: int
    headline_count: int
    candidate_count: int
    watchlist_symbols: List[str]
    headlines: List[NewsItem]
    candidates: List[StockCandidate]
    stage: str
    reason: str


@dataclass
class WatchlistNewsDigest:
    instrument: Instrument
    stock_news: List[NewsItem]
    industry_news: Dict[str, List[NewsItem]]
    insight: str
    risk: str


@dataclass
class BiweeklyInsights:
    generated_at: datetime
    market_news_count: int
    stock_news_count: int
    candidate_count: int
    theme_insights: List[ThemeInsight]
    next_hotspots: List[ThemeInsight]
    watchlist_digests: List[WatchlistNewsDigest]
    observation_pool: List[StockCandidate]


def run_insights(config: AgentConfig, force: bool = False) -> str:
    if not force:
        cached = latest_fresh_report(config.output_dir)
        if cached:
            current_path = Path(config.output_dir) / f"{INSIGHTS_PREFIX}_latest.md"
            if cached != current_path:
                current_path.write_text(cached.read_text(encoding="utf-8"), encoding="utf-8")
                return str(current_path)
            return str(cached)

    report = build_biweekly_insights(config)
    path = write_insights_report(config.output_dir, report)
    return str(path)


def latest_fresh_report(
    output_dir: str,
    interval_days: int = INSIGHTS_INTERVAL_DAYS,
) -> Optional[Path]:
    report_dir = Path(output_dir)
    if not report_dir.exists():
        return None
    now = datetime.now()
    current_path = report_dir / f"{INSIGHTS_PREFIX}_latest.md"
    if current_path.exists():
        generated_at = datetime.fromtimestamp(current_path.stat().st_mtime)
        if now - generated_at <= timedelta(days=interval_days):
            return current_path
    for path in sorted(
        report_dir.glob(f"{INSIGHTS_PREFIX}_*.md"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        generated_at = datetime.fromtimestamp(path.stat().st_mtime)
        if now - generated_at <= timedelta(days=interval_days):
            return path
    return None


def build_biweekly_insights(config: AgentConfig) -> BiweeklyInsights:
    market_news = recent_news(dedupe_news(fetch_news(config, limit_per_source=60)))
    candidates = fetch_hot_candidates(limit=36, keyword_limit=18)
    stock_news_by_symbol = collect_watchlist_stock_news(config.watchlist, market_news)
    theme_insights = build_theme_insights(config, market_news, candidates)
    watchlist_digests = build_watchlist_digests(
        config.watchlist,
        market_news,
        stock_news_by_symbol,
        theme_insights,
    )
    next_hotspots = select_next_hotspots(theme_insights)
    observation_pool = select_observation_pool(config.watchlist, candidates)
    stock_news_count = sum(len(items) for items in stock_news_by_symbol.values())
    return BiweeklyInsights(
        generated_at=datetime.now(),
        market_news_count=len(market_news),
        stock_news_count=stock_news_count,
        candidate_count=len(candidates),
        theme_insights=theme_insights,
        next_hotspots=next_hotspots,
        watchlist_digests=watchlist_digests,
        observation_pool=observation_pool,
    )


def collect_watchlist_stock_news(
    watchlist: Sequence[Instrument],
    market_news: Sequence[NewsItem],
    limit: int = 8,
) -> Dict[str, List[NewsItem]]:
    news_by_symbol: Dict[str, List[NewsItem]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {
            pool.submit(fetch_stock_news, provider_symbol(instrument), limit): instrument
            for instrument in watchlist
        }
        for future in as_completed(future_map):
            instrument = future_map[future]
            try:
                items = recent_news(dedupe_news(future.result()))
            except Exception:
                items = []
            if not items:
                items = fallback_stock_news(instrument, market_news, limit=limit)
            news_by_symbol[instrument.symbol] = items[:limit]
    return news_by_symbol


def build_theme_insights(
    config: AgentConfig,
    market_news: Sequence[NewsItem],
    candidates: Sequence[StockCandidate],
) -> List[ThemeInsight]:
    keyword_map = theme_keywords(config)
    news_by_theme: Dict[str, List[NewsItem]] = {theme: [] for theme in keyword_map}
    candidates_by_theme: Dict[str, List[StockCandidate]] = {
        theme: [] for theme in keyword_map
    }
    watchlist_by_theme = watchlist_symbols_by_theme(config)

    for item in market_news:
        text = f"{item.title} {item.summary}"
        for theme, keywords in keyword_map.items():
            if contains_any(text, keywords):
                news_by_theme.setdefault(theme, []).append(item)

    for candidate in candidates:
        text = f"{candidate.name} {candidate.reason}"
        matched = set(candidate_themes(candidate))
        for theme, keywords in keyword_map.items():
            if theme in matched or contains_any(text, keywords):
                candidates_by_theme.setdefault(theme, []).append(candidate)

    themes = sorted(
        set(keyword_map)
        | set(news_by_theme)
        | set(candidates_by_theme)
        | set(watchlist_by_theme)
    )
    insights: List[ThemeInsight] = []
    for theme in themes:
        headlines = dedupe_news(news_by_theme.get(theme, []))
        theme_candidates = dedupe_candidates(candidates_by_theme.get(theme, []))
        watch_symbols = sorted(set(watchlist_by_theme.get(theme, [])))
        if not headlines and not theme_candidates and not watch_symbols:
            continue
        score = len(headlines) * 2 + len(theme_candidates) * 3 + len(watch_symbols)
        stage = theme_stage(len(headlines), len(theme_candidates), len(watch_symbols))
        reason = theme_reason(theme, headlines, theme_candidates, watch_symbols, stage)
        insights.append(
            ThemeInsight(
                theme=theme,
                score=score,
                headline_count=len(headlines),
                candidate_count=len(theme_candidates),
                watchlist_symbols=watch_symbols,
                headlines=headlines[:6],
                candidates=theme_candidates[:8],
                stage=stage,
                reason=reason,
            )
        )
    return sorted(insights, key=lambda item: item.score, reverse=True)


def build_watchlist_digests(
    watchlist: Sequence[Instrument],
    market_news: Sequence[NewsItem],
    stock_news_by_symbol: Dict[str, List[NewsItem]],
    theme_insights: Sequence[ThemeInsight],
) -> List[WatchlistNewsDigest]:
    themes_by_name = {item.theme: item for item in theme_insights}
    digests: List[WatchlistNewsDigest] = []
    for instrument in watchlist:
        themes = instrument.themes or themes_for_text(instrument.name)
        industry_news: Dict[str, List[NewsItem]] = {}
        for theme in themes:
            insight = themes_by_name.get(theme)
            if insight:
                industry_news[theme] = insight.headlines[:3]
            else:
                industry_news[theme] = match_news_by_theme(theme, market_news)[:3]
        stock_news = stock_news_by_symbol.get(instrument.symbol, [])
        digests.append(
            WatchlistNewsDigest(
                instrument=instrument,
                stock_news=stock_news[:5],
                industry_news=industry_news,
                insight=watchlist_insight(instrument, stock_news, themes, themes_by_name),
                risk=watchlist_risk(stock_news, themes, themes_by_name),
            )
        )
    return digests


def select_next_hotspots(theme_insights: Sequence[ThemeInsight]) -> List[ThemeInsight]:
    preferred = [
        insight
        for insight in theme_insights
        if insight.headline_count >= 1
        and insight.stage in {"早期观察", "产业升温", "热点扩散"}
    ]
    if not preferred:
        preferred = [item for item in theme_insights if item.headline_count or item.candidate_count]
    return list(preferred[:5])


def select_observation_pool(
    watchlist: Sequence[Instrument],
    candidates: Sequence[StockCandidate],
    limit: int = 14,
) -> List[StockCandidate]:
    watch_keys = {symbol_key(instrument.symbol) for instrument in watchlist}
    watch_names = {normalize(instrument.name) for instrument in watchlist}
    output: List[StockCandidate] = []
    for candidate in dedupe_candidates(list(candidates)):
        candidate_name = normalize(candidate.name)
        if (
            symbol_key(candidate.symbol) in watch_keys
            or candidate_name in watch_names
            or any(name and name in candidate_name for name in watch_names)
        ):
            continue
        output.append(candidate)
        if len(output) >= limit:
            break
    return output


def write_insights_report(output_dir: str, report: BiweeklyInsights) -> Path:
    return write_report_file(
        output_dir,
        INSIGHTS_PREFIX,
        render_insights_report(report),
        archive=True,
    )


def render_insights_report(report: BiweeklyInsights) -> str:
    lines = [
        "# 两周市场洞察",
        "",
        f"- 生成时间：{report.generated_at.strftime('%Y-%m-%d %H:%M')}",
        f"- 更新频率：默认每 {INSIGHTS_INTERVAL_DAYS} 天生成一次，未过期时复用最近报告",
        f"- 覆盖数据：市场新闻 {report.market_news_count} 条，自选股专项新闻 {report.stock_news_count} 条，人气候选 {report.candidate_count} 个",
        "",
        "## 核心 Insights",
        "",
    ]
    if report.next_hotspots:
        for insight in report.next_hotspots:
            related = ", ".join(insight.watchlist_symbols) or "暂无自选映射"
            lines.append(
                f"- {insight.theme}：{insight.stage}，{insight.reason}；自选映射：{related}"
            )
    else:
        lines.append("- 暂未识别出足够清晰的下一热点候选，建议等待更多新闻密度和人气榜信号。")

    lines.extend(["", "## 下一热点候选", ""])
    lines.extend(render_theme_table(report.next_hotspots or report.theme_insights[:5]))

    lines.extend(["", "## 自选股专项新闻", ""])
    for digest in report.watchlist_digests:
        instrument = digest.instrument
        theme_text = "、".join(instrument.themes) or "未配置"
        lines.extend(
            [
                f"### {instrument.symbol} {instrument.name}",
                "",
                f"- 所在产业：{theme_text}",
                f"- 洞察：{digest.insight}",
                f"- 风险：{digest.risk}",
                "- 个股新闻：",
            ]
        )
        if digest.stock_news:
            for item in digest.stock_news[:3]:
                lines.append(f"  - {news_line(item)}")
        else:
            lines.append("  - 暂无可用专项新闻。")
        lines.append("- 产业新闻：")
        industry_lines = 0
        for theme, items in digest.industry_news.items():
            for item in items[:2]:
                lines.append(f"  - [{theme}] {news_line(item)}")
                industry_lines += 1
        if industry_lines == 0:
            lines.append("  - 暂无可用产业新闻。")
        lines.append("")

    if report.observation_pool:
        lines.extend(["## 推荐观察池", ""])
        for candidate in report.observation_pool:
            theme_text = "、".join(candidate_themes(candidate)) or "待二次归因"
            lines.append(
                f"- {candidate_label(candidate)}（{candidate.market}，排名 {candidate.score}，涨跌 {candidate.change_pct:.2f}%，主题：{theme_text}）：{candidate.reason}"
            )
        lines.append("")

    lines.extend(["## 数据来源和边界", ""])
    lines.append(
        "本报告聚合公开新闻、财经快讯、个股新闻和市场人气榜，输出的是信息密度与潜在主题迁移，不构成投资建议。"
    )
    return "\n".join(lines) + "\n"


def render_theme_table(insights: Sequence[ThemeInsight]) -> List[str]:
    lines = [
        "| 主题 | 分数 | 新闻 | 人气股 | 自选映射 | 阶段 | 判断 |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for insight in insights:
        related = ", ".join(insight.watchlist_symbols) or "-"
        lines.append(
            f"| {insight.theme} | {insight.score} | {insight.headline_count} | {insight.candidate_count} | {related} | {insight.stage} | {insight.reason} |"
        )
    return lines


def theme_keywords(config: AgentConfig) -> Dict[str, List[str]]:
    keyword_map = {theme: list(keywords) for theme, keywords in DEFAULT_THEME_KEYWORDS.items()}
    for theme in config.theme_stock_map:
        keyword_map.setdefault(theme, [theme])
    for instrument in config.watchlist:
        for theme in instrument.themes:
            keyword_map.setdefault(theme, [theme])
    return keyword_map


def watchlist_symbols_by_theme(config: AgentConfig) -> Dict[str, List[str]]:
    by_theme: Dict[str, List[str]] = {}
    for theme, symbols in config.theme_stock_map.items():
        by_theme.setdefault(theme, []).extend(symbols)
    for instrument in config.watchlist:
        for theme in instrument.themes:
            by_theme.setdefault(theme, []).append(instrument.symbol)
    return by_theme


def match_news_by_theme(theme: str, items: Sequence[NewsItem]) -> List[NewsItem]:
    keywords = DEFAULT_THEME_KEYWORDS.get(theme, [theme])
    return [item for item in items if contains_any(f"{item.title} {item.summary}", keywords)]


def candidate_themes(candidate: StockCandidate) -> List[str]:
    text = f"{candidate.name} {candidate.reason}"
    themes = themes_for_text(text)
    for name_part, hinted_themes in CANDIDATE_THEME_HINTS.items():
        if name_part.lower() in text.lower():
            themes.extend(hinted_themes)
    return unique(themes)


def fallback_stock_news(
    instrument: Instrument,
    market_news: Sequence[NewsItem],
    limit: int,
) -> List[NewsItem]:
    keywords = [instrument.symbol, instrument.name]
    return [
        item
        for item in market_news
        if contains_any(f"{item.title} {item.summary}", keywords)
    ][:limit]


def watchlist_insight(
    instrument: Instrument,
    stock_news: Sequence[NewsItem],
    themes: Sequence[str],
    themes_by_name: Dict[str, ThemeInsight],
) -> str:
    hot_themes = [themes_by_name[theme] for theme in themes if theme in themes_by_name]
    if stock_news and hot_themes:
        top = max(hot_themes, key=lambda item: item.score)
        return f"个股新闻有更新，且 {top.theme} 处于{top.stage}，适合纳入重点跟踪。"
    if stock_news:
        return "个股新闻有更新，先判断是否影响业绩、订单、产能或监管预期。"
    if hot_themes:
        top = max(hot_themes, key=lambda item: item.score)
        return f"个股暂无明显新闻，但所在产业 {top.theme} 信息密度较高，可观察是否出现补涨或分化。"
    return "个股和产业新闻密度都不高，以技术结构和公告为主。"


def watchlist_risk(
    stock_news: Sequence[NewsItem],
    themes: Sequence[str],
    themes_by_name: Dict[str, ThemeInsight],
) -> str:
    crowded = [
        themes_by_name[theme].theme
        for theme in themes
        if theme in themes_by_name and themes_by_name[theme].stage == "高关注"
    ]
    if crowded:
        return f"{'、'.join(crowded)} 已进入高关注区，追高前需要等换手和回撤确认。"
    if any(negative_news(item) for item in stock_news):
        return "个股新闻出现业绩/减持/监管等负面关键词，需降低仓位假设。"
    return "主要风险来自新闻热度不足、题材轮动过快和财报兑现不及预期。"


def theme_stage(headline_count: int, candidate_count: int, watchlist_count: int) -> str:
    if candidate_count >= 5:
        return "高关注"
    if headline_count >= 4 and candidate_count >= 1:
        return "热点扩散"
    if headline_count >= 2 and watchlist_count >= 1:
        return "产业升温"
    if headline_count >= 1 or candidate_count >= 1:
        return "早期观察"
    return "低热度"


def theme_reason(
    theme: str,
    headlines: Sequence[NewsItem],
    candidates: Sequence[StockCandidate],
    watch_symbols: Sequence[str],
    stage: str,
) -> str:
    parts = []
    if headlines:
        parts.append(f"近两周相关新闻 {len(headlines)} 条")
    if candidates:
        parts.append(f"人气榜相关标的 {len(candidates)} 个")
    if watch_symbols:
        parts.append(f"自选股覆盖 {len(set(watch_symbols))} 只")
    if not parts:
        parts.append("当前信号偏弱")
    if stage == "高关注":
        parts.append("热度已较集中，适合等分歧而不是追涨")
    elif stage == "早期观察":
        parts.append("热度仍在早期，可继续看新闻密度是否上行")
    return "，".join(parts)


def recent_news(items: Iterable[NewsItem], days: int = INSIGHTS_INTERVAL_DAYS) -> List[NewsItem]:
    cutoff = datetime.now() - timedelta(days=days)
    output = []
    for item in items:
        if item.published_at is None:
            output.append(item)
            continue
        published_at = item.published_at.replace(tzinfo=None)
        if published_at >= cutoff:
            output.append(item)
    return output


def dedupe_news(items: Iterable[NewsItem]) -> List[NewsItem]:
    output: List[NewsItem] = []
    seen = set()
    for item in items:
        key = normalize(item.title or item.summary)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def unique(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def contains_any(text: str, keywords: Sequence[str]) -> bool:
    value = text.lower()
    return any(keyword.lower() in value for keyword in keywords)


def provider_symbol(instrument: Instrument) -> str:
    return instrument.provider_symbol or instrument.symbol


def symbol_key(value: str) -> str:
    return value.upper().replace("SH", "").replace("SZ", "").replace("HK", "").lstrip("0") or value


def news_line(item: NewsItem) -> str:
    link = f" [{item.source}]({item.link})" if item.link else f" {item.source}"
    date_text = item.published_at.strftime("%m-%d ") if item.published_at else ""
    return f"{date_text}{item.title}{link}"


def negative_news(item: NewsItem) -> bool:
    text = f"{item.title} {item.summary}"
    keywords = ["减持", "亏损", "下滑", "问询", "处罚", "立案", "终止", "不及预期"]
    return contains_any(text, keywords)
