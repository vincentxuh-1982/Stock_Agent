from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from .config import AgentConfig, load_config
from .digest import instruments_for_positions
from .insights import latest_fresh_report
from .models import Instrument, Portfolio
from .notifier import notify_report
from .pipeline import run_daily_digest, run_insights, run_news, run_realtime_push
from .portfolio_manager import load_portfolio_file
from .trading_hours import split_by_session


def run_scheduler(
    config: AgentConfig,
    portfolio: Optional[Portfolio] = None,
    poll_seconds: int = 60,
    config_path: Optional[str] = None,
    portfolio_path: Optional[str] = None,
) -> None:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "scheduler_state.json"
    state = load_state(state_path)
    timezone = ZoneInfo(config.timezone)

    while True:
        current_config = load_config(config_path) if config_path else config
        current_portfolio = (
            load_portfolio_file(portfolio_path)
            if portfolio_path
            else portfolio
        )
        timezone = ZoneInfo(current_config.timezone)
        now = datetime.now(timezone)
        if now.weekday() < 5:
            maybe_run("midday_news", current_config, state, now, lambda: run_news(current_config))
            maybe_run("evening_news", current_config, state, now, lambda: run_news(current_config))
            maybe_run_biweekly_insights(current_config, state, now)
            maybe_run_and_push(
                job_name="after_close_review",
                config=current_config,
                state=state,
                now=now,
                callback=lambda: run_daily_digest(current_config, current_portfolio),
                title="每日复盘与持仓简报",
            )
            maybe_run_realtime_push(current_config, current_portfolio, state, now)
            save_state(state_path, state)
        time.sleep(poll_seconds)


def maybe_run(
    job_name: str,
    config: AgentConfig,
    state: Dict[str, str],
    now: datetime,
    callback,
) -> None:
    scheduled_at = config.schedules.get(job_name)
    if not scheduled_at:
        return
    hour, minute = [int(part) for part in scheduled_at.split(":", 1)]
    if now.hour < hour or (now.hour == hour and now.minute < minute):
        return

    state_key = f"{job_name}:{now.date().isoformat()}"
    if state.get(state_key):
        return

    path = callback()
    state[state_key] = str(path)


def maybe_run_and_push(
    job_name: str,
    config: AgentConfig,
    state: Dict[str, str],
    now: datetime,
    callback: Callable[[], str],
    title: str,
) -> None:
    if not scheduled_time_reached(job_name, config, now):
        return
    state_key = f"{job_name}:{now.date().isoformat()}"
    if state.get(state_key):
        return
    path = callback()
    push_result = notify_report(config, title, path)
    state[state_key] = f"{path}; push={push_result.sent}; {push_result.message}"
    print(f"{title}: {path}; push={push_result.sent}; {push_result.message}", flush=True)


def maybe_run_realtime_push(
    config: AgentConfig,
    portfolio: Optional[Portfolio],
    state: Dict[str, str],
    now: datetime,
) -> None:
    interval = realtime_push_interval_minutes(config)
    if interval <= 0:
        return
    if not any_market_active(config, portfolio, now):
        return
    slot = (now.hour * 60 + now.minute) // interval
    state_key = f"realtime_push:{now.date().isoformat()}:{slot}"
    if state.get(state_key):
        return
    path = run_realtime_push(config, portfolio)
    push_result = notify_report(config, "盘中实时策略简报", path)
    state[state_key] = f"{path}; push={push_result.sent}; {push_result.message}"
    print(f"盘中实时策略简报: {path}; push={push_result.sent}; {push_result.message}", flush=True)


def maybe_run_biweekly_insights(
    config: AgentConfig,
    state: Dict[str, str],
    now: datetime,
) -> None:
    if latest_fresh_report(config.output_dir):
        return
    maybe_run_and_push(
        job_name="biweekly_insights",
        config=config,
        state=state,
        now=now,
        callback=lambda: run_insights(config, force=True),
        title="两周市场洞察",
    )


def scheduled_time_reached(
    job_name: str,
    config: AgentConfig,
    now: datetime,
) -> bool:
    scheduled_at = config.schedules.get(job_name)
    if not scheduled_at:
        return False
    hour, minute = [int(part) for part in scheduled_at.split(":", 1)]
    return now.hour > hour or (now.hour == hour and now.minute >= minute)


def realtime_push_interval_minutes(config: AgentConfig) -> int:
    raw = config.schedules.get("realtime_push_interval_minutes", "30")
    try:
        return max(5, int(raw))
    except ValueError:
        return 30


def any_market_active(
    config: AgentConfig,
    portfolio: Optional[Portfolio],
    now: datetime,
) -> bool:
    instruments: List[Instrument] = list(config.indices + config.watchlist)
    if portfolio:
        active_positions = [position for position in portfolio.positions if position.shares > 0]
        instruments.extend(instruments_for_positions(config, active_positions))
    active, _ = split_by_session(instruments, now=now, timezone=config.timezone)
    return bool(active)


def load_state(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: Dict[str, str]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
