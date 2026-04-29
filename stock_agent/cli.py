from __future__ import annotations

import argparse

from .config import load_config, load_mapping
from .models import Portfolio
from .pipeline import run_insights, run_news, run_portfolio, run_realtime, run_review
from .scheduler import run_scheduler
from .webapp import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock analysis agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("review", "news", "realtime", "insights", "run-all", "schedule"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--config", default="config/demo.json")
        if command == "insights":
            subparser.add_argument("--force", action="store_true")
        if command in {"run-all", "schedule"}:
            subparser.add_argument("--portfolio", default=None)
        if command == "schedule":
            subparser.add_argument("--poll-seconds", type=int, default=60)

    web = subparsers.add_parser("web")
    web.add_argument("--config", default="config/local.json")
    web.add_argument("--portfolio", default="data/portfolio.local.json")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    advise = subparsers.add_parser("advise")
    advise.add_argument("--config", default="config/demo.json")
    advise.add_argument("--portfolio", required=True)

    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "review":
        path, _, _ = run_review(config)
        print(f"market review: {path}")
        return

    if args.command == "news":
        path = run_news(config)
        print(f"news report: {path}")
        return

    if args.command == "realtime":
        path = run_realtime(config)
        print(f"realtime report: {path}")
        return

    if args.command == "insights":
        path = run_insights(config, force=args.force)
        print(f"biweekly insights: {path}")
        return

    if args.command == "advise":
        portfolio = load_portfolio(args.portfolio)
        path = run_portfolio(config, portfolio)
        print(f"portfolio advice: {path}")
        return

    if args.command == "run-all":
        portfolio = load_portfolio(args.portfolio) if args.portfolio else None
        review_path, _, _ = run_review(config)
        print(f"market review: {review_path}")
        realtime_path = run_realtime(config)
        print(f"realtime report: {realtime_path}")
        news_path = run_news(config)
        print(f"news report: {news_path}")
        insights_path = run_insights(config)
        print(f"biweekly insights: {insights_path}")
        if portfolio:
            advice_path = run_portfolio(config, portfolio)
            print(f"portfolio advice: {advice_path}")
        return

    if args.command == "schedule":
        portfolio = load_portfolio(args.portfolio) if args.portfolio else None
        run_scheduler(config, portfolio=portfolio, poll_seconds=args.poll_seconds)
        return

    if args.command == "web":
        serve(args.config, args.portfolio, args.host, args.port)


def load_portfolio(path: str) -> Portfolio:
    raw = load_mapping(path)
    return Portfolio.from_dict(raw)


if __name__ == "__main__":
    main()
