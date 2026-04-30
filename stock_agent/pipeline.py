from __future__ import annotations

from typing import List, Optional, Tuple

from .analyzer import analyze_many
from .config import AgentConfig
from .data_providers import provider_from_config
from .digest import run_daily_digest as run_daily_digest_report
from .digest import run_realtime_push_digest
from .insights import run_insights as run_biweekly_insights
from .models import AnalysisResult, Portfolio
from .news import fetch_hot_candidates, fetch_news, find_hotspots
from .realtime import run_realtime_analysis
from .reports import (
    write_market_review,
    write_news_report,
    write_portfolio_report,
    write_realtime_report,
)


def run_review(config: AgentConfig) -> Tuple[str, List[AnalysisResult], List[AnalysisResult]]:
    provider = provider_from_config(config)
    errors: List[str] = []
    index_results = analyze_many(config.indices, provider, config, errors=errors)
    watch_results = analyze_many(config.watchlist, provider, config, errors=errors)
    path = write_market_review(config.output_dir, index_results, watch_results, errors)
    return str(path), index_results, watch_results


def run_news(config: AgentConfig) -> str:
    items = fetch_news(config)
    candidates = fetch_hot_candidates()
    hotspots = find_hotspots(config, items, candidates)
    path = write_news_report(config.output_dir, hotspots, candidates)
    return str(path)


def run_insights(config: AgentConfig, force: bool = False) -> str:
    return run_biweekly_insights(config, force=force)


def run_daily_digest(config: AgentConfig, portfolio: Optional[Portfolio] = None) -> str:
    return run_daily_digest_report(config, portfolio)


def run_realtime_push(config: AgentConfig, portfolio: Optional[Portfolio] = None) -> str:
    return run_realtime_push_digest(config, portfolio)


def run_realtime(config: AgentConfig) -> str:
    results, inactive, errors = run_realtime_analysis(config)
    path = write_realtime_report(config.output_dir, results, inactive, errors)
    return str(path)


def run_portfolio(config: AgentConfig, portfolio: Portfolio) -> str:
    provider = provider_from_config(config)
    instruments = []
    known = {item.symbol: item for item in config.watchlist + config.indices}
    for position in portfolio.positions:
        if position.shares <= 0:
            continue
        instrument = known.get(position.symbol)
        if instrument is None:
            from .models import Instrument

            instrument = Instrument(
                symbol=position.symbol,
                name=position.name,
                kind=position.kind,
                market=position.market,
                provider_symbol=position.provider_symbol,
            )
        instruments.append(instrument)

    errors: List[str] = []
    results = analyze_many(instruments, provider, config, errors=errors)
    path = write_portfolio_report(
        config.output_dir,
        results,
        portfolio.positions,
        errors,
    )
    return str(path)
