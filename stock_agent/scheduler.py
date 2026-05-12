from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo

from .config import AgentConfig, load_config
from .insights import latest_fresh_report
from .models import Portfolio
from .notifier import notify_report
from .pipeline import (
    run_daily_digest,
    run_insights,
    run_news,
    run_opening_brief,
    run_realtime_push,
)
from .portfolio_manager import load_portfolio_file


@dataclass(frozen=True)
class PushJob:
    name: str
    default_time: str
    title: str
    kind: str
    focus_note: str = ""


TIMED_PUSH_JOBS = [
    PushJob(
        name="opening_brief",
        default_time="09:15",
        title="开盘早知道",
        kind="opening",
        focus_note="持仓股票今日策略，自选股加仓/观望计划。",
    ),
    PushJob(
        name="morning_flash",
        default_time="10:30",
        title="早盘快讯",
        kind="realtime",
        focus_note="早盘走势、异动、策略更新和今日关注点。",
    ),
    PushJob(
        name="midday_flash",
        default_time="11:30",
        title="午间快讯",
        kind="realtime",
        focus_note="上午走势复盘，下午策略和关注点。",
    ),
    PushJob(
        name="golden_1430",
        default_time="14:30",
        title="黄金两点半",
        kind="realtime",
        focus_note="临近尾盘的走势、风险位和尾盘策略。",
    ),
    PushJob(
        name="daily_summary",
        default_time="15:00",
        title="今日总结",
        kind="daily",
        focus_note="全天走势、关键信息和明日策略。",
    ),
]


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
            maybe_run_timed_pushes(current_config, current_portfolio, state, now)
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


def maybe_run_timed_pushes(
    config: AgentConfig,
    portfolio: Optional[Portfolio],
    state: Dict[str, str],
    now: datetime,
) -> None:
    for job in TIMED_PUSH_JOBS:
        if not scheduled_time_due(job.name, config, now, default_time=job.default_time):
            continue
        state_key = f"timed_push:{job.name}:{now.date().isoformat()}"
        if state.get(state_key):
            continue
        path = run_timed_push_job(job, config, portfolio)
        push_result = notify_report(config, job.title, path)
        state[state_key] = f"{path}; push={push_result.sent}; {push_result.message}"
        print(f"{job.title}: {path}; push={push_result.sent}; {push_result.message}", flush=True)


def run_timed_push_job(
    job: PushJob,
    config: AgentConfig,
    portfolio: Optional[Portfolio],
) -> str:
    if job.kind == "opening":
        return run_opening_brief(config, portfolio)
    if job.kind == "daily":
        return run_daily_digest(config, portfolio, title=job.title)
    return run_realtime_push(
        config,
        portfolio,
        title=job.title,
        focus_note=job.focus_note,
    )


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
    default_time: str = "",
) -> bool:
    scheduled_at = config.schedules.get(job_name, default_time)
    if not scheduled_at:
        return False
    hour, minute = [int(part) for part in scheduled_at.split(":", 1)]
    return now.hour > hour or (now.hour == hour and now.minute >= minute)


def scheduled_time_due(
    job_name: str,
    config: AgentConfig,
    now: datetime,
    default_time: str,
    grace_minutes: int = 20,
) -> bool:
    scheduled_at = config.schedules.get(job_name, default_time)
    if not scheduled_at:
        return False
    hour, minute = [int(part) for part in scheduled_at.split(":", 1)]
    current_minutes = now.hour * 60 + now.minute
    scheduled_minutes = hour * 60 + minute
    delta = current_minutes - scheduled_minutes
    return 0 <= delta <= grace_minutes


def load_state(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: Dict[str, str]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
