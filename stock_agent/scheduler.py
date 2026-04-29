from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from .config import AgentConfig
from .models import Portfolio
from .pipeline import run_insights, run_news, run_portfolio, run_review


def run_scheduler(
    config: AgentConfig,
    portfolio: Optional[Portfolio] = None,
    poll_seconds: int = 60,
) -> None:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "scheduler_state.json"
    state = load_state(state_path)
    timezone = ZoneInfo(config.timezone)

    while True:
        now = datetime.now(timezone)
        if now.weekday() < 5:
            maybe_run("midday_news", config, state, now, lambda: run_news(config))
            maybe_run("evening_news", config, state, now, lambda: run_news(config))
            maybe_run("biweekly_insights", config, state, now, lambda: run_insights(config))
            maybe_run(
                "after_close_review",
                config,
                state,
                now,
                lambda: run_after_close(config, portfolio),
            )
            save_state(state_path, state)
        time.sleep(poll_seconds)


def run_after_close(config: AgentConfig, portfolio: Optional[Portfolio]) -> str:
    review_path, _, _ = run_review(config)
    if portfolio:
        portfolio_path = run_portfolio(config, portfolio)
        return f"{review_path}; {portfolio_path}"
    return review_path


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


def load_state(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: Dict[str, str]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
