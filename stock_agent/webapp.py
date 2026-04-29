from __future__ import annotations

import argparse
import html
import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .assistant import answer_question, assistant_status
from .config import AgentConfig, load_config, load_mapping
from .models import Portfolio
from .portfolio_manager import (
    add_position,
    delete_position,
    load_portfolio_file,
    record_trade,
)
from .pipeline import run_insights, run_news, run_portfolio, run_realtime, run_review
from .search import search_instruments


REPORT_KINDS = {
    "biweekly_insights": "insights",
    "market_review": "review",
    "realtime_watchlist": "realtime",
    "news_hotspots": "news",
    "portfolio_advice": "portfolio",
}


class WebApp:
    def __init__(self, config_path: str, portfolio_path: Optional[str]) -> None:
        self.config_path = config_path
        self.portfolio_path = portfolio_path
        self.lock = threading.Lock()

    def load_config(self) -> AgentConfig:
        return load_config(self.config_path)

    def load_portfolio(self) -> Optional[Portfolio]:
        if not self.portfolio_path:
            return None
        return load_portfolio_file(self.portfolio_path)

    def state(self) -> Dict[str, object]:
        config = self.load_config()
        portfolio = self.load_portfolio()
        return {
            "config_path": self.config_path,
            "portfolio_path": self.portfolio_path,
            "indices": [instrument.__dict__ for instrument in config.indices],
            "watchlist": [instrument.__dict__ for instrument in config.watchlist],
            "portfolio_count": len(portfolio.positions) if portfolio else 0,
            "reports": self.list_reports(config),
            "assistant": assistant_status(),
        }

    def list_reports(self, config: Optional[AgentConfig] = None) -> List[Dict[str, object]]:
        cfg = config or self.load_config()
        report_dir = Path(cfg.output_dir)
        if not report_dir.exists():
            return []
        by_kind: Dict[str, Dict[str, object]] = {}
        for path in report_dir.glob("*.md"):
            kind = report_kind(path.name)
            if kind == "other":
                continue
            report = {
                "name": path.name,
                "kind": kind,
                "mtime": path.stat().st_mtime,
                "title": report_title(path),
                "size": path.stat().st_size,
                "current": path.name.endswith("_latest.md"),
            }
            previous = by_kind.get(kind)
            if previous is None or report_rank(report) > report_rank(previous):
                by_kind[kind] = report
        return sorted(by_kind.values(), key=lambda item: item["mtime"], reverse=True)

    def list_history(self, kind: str = "") -> List[Dict[str, object]]:
        config = self.load_config()
        history_dir = Path(config.output_dir) / "history"
        if not history_dir.exists():
            return []
        reports = []
        for path in history_dir.glob("*.md"):
            report_kind_value = report_kind(path.name)
            if kind and report_kind_value != kind:
                continue
            reports.append(
                {
                    "name": path.name,
                    "kind": report_kind_value,
                    "mtime": path.stat().st_mtime,
                    "title": report_title(path),
                    "size": path.stat().st_size,
                }
            )
        return sorted(reports, key=lambda item: item["mtime"], reverse=True)

    def latest_report(self, kind: str) -> Optional[Dict[str, object]]:
        for report in self.list_reports():
            if report["kind"] == kind:
                return report
        return None

    def read_report(self, name: str) -> Dict[str, object]:
        config = self.load_config()
        report_dir = Path(config.output_dir).resolve()
        path = (report_dir / name).resolve()
        if report_dir not in path.parents and path != report_dir:
            raise ValueError("invalid report path")
        if not path.exists() or path.suffix != ".md":
            raise FileNotFoundError(name)
        markdown = path.read_text(encoding="utf-8")
        return {
            "name": path.name,
            "kind": report_kind(path.name),
            "title": first_heading(markdown) or path.name,
            "markdown": markdown,
            "html": markdown_to_html(markdown),
        }

    def run_job(self, job: str) -> Dict[str, object]:
        with self.lock:
            config = self.load_config()
            portfolio = self.load_portfolio()
            paths: List[str] = []
            if job == "realtime":
                paths.append(run_realtime(config))
            elif job == "review":
                path, _, _ = run_review(config)
                paths.append(path)
            elif job == "news":
                paths.append(run_news(config))
            elif job == "insights":
                paths.append(run_insights(config))
            elif job == "portfolio":
                if portfolio is None:
                    raise ValueError("portfolio file is missing")
                paths.append(run_portfolio(config, portfolio))
            elif job == "run_all":
                review_path, _, _ = run_review(config)
                paths.append(review_path)
                paths.append(run_realtime(config))
                paths.append(run_news(config))
                paths.append(run_insights(config))
                if portfolio is not None:
                    paths.append(run_portfolio(config, portfolio))
            else:
                raise ValueError(f"unsupported job: {job}")

            reports = [self.read_report(Path(path).name) for path in paths]
            return {"paths": paths, "reports": reports, "all_reports": self.list_reports(config)}

    def search(self, query: str) -> Dict[str, object]:
        config = self.load_config()
        return {"results": search_instruments(config, query)}

    def portfolio_view(self) -> Dict[str, object]:
        portfolio = self.load_portfolio() or Portfolio(cash=0, positions=[])
        return build_portfolio_view(portfolio, self)

    def add_position(self, payload: Dict[str, object]) -> Dict[str, object]:
        if not self.portfolio_path:
            raise ValueError("portfolio file is missing")
        with self.lock:
            portfolio = add_position(
                self.portfolio_path,
                payload,
                self.load_config(),
            )
            return {
                "message": "已新增持仓",
                "portfolio": build_portfolio_view(portfolio, self),
                "state": self.state(),
            }

    def delete_position(self, symbol: str) -> Dict[str, object]:
        if not self.portfolio_path:
            raise ValueError("portfolio file is missing")
        with self.lock:
            portfolio = delete_position(self.portfolio_path, symbol)
            return {
                "message": f"已删除 {symbol}",
                "portfolio": build_portfolio_view(portfolio, self),
                "state": self.state(),
            }

    def record_trade(self, payload: Dict[str, object]) -> Dict[str, object]:
        if not self.portfolio_path:
            raise ValueError("portfolio file is missing")
        with self.lock:
            portfolio, trade = record_trade(
                self.portfolio_path,
                payload,
                self.load_config(),
            )
            return {
                "message": "交易已记录",
                "trade": trade.to_dict(),
                "portfolio": build_portfolio_view(portfolio, self),
                "state": self.state(),
            }

    def ask_assistant(self, payload: Dict[str, object]) -> Dict[str, object]:
        config = self.load_config()
        portfolio = self.load_portfolio()
        kind = str(payload.get("kind", "realtime")).strip() or "realtime"
        question = str(payload.get("question", "")).strip()
        report_name = str(payload.get("report", "")).strip()
        if not report_name:
            latest = self.latest_report(kind)
            report_name = str(latest["name"]) if latest else ""
        report_markdown = ""
        if report_name:
            try:
                report_markdown = str(self.read_report(report_name)["markdown"])
            except (FileNotFoundError, ValueError):
                report_markdown = ""
        response = answer_question(
            question=question,
            kind=kind,
            report_markdown=report_markdown,
            config=config,
            portfolio=portfolio,
        )
        response["report"] = report_name
        return response

    def add_instrument(self, payload: Dict[str, object]) -> Dict[str, object]:
        with self.lock:
            raw = load_mapping(self.config_path)
            kind = str(payload.get("kind", "")).strip()
            symbol = str(payload.get("symbol", "")).strip()
            name = str(payload.get("name", symbol)).strip()
            if not symbol or not name or not kind:
                raise ValueError("symbol, name and kind are required")

            is_index = kind.endswith("index")
            list_key = "indices" if is_index else "watchlist"
            instruments = list(raw.get(list_key, []))
            if any(str(item.get("symbol", "")).strip() == symbol for item in instruments):
                return {
                    "added": False,
                    "message": f"{symbol} {name} 已存在",
                    "state": self.state(),
                }

            market = str(payload.get("market", "HK" if kind.startswith("hk") else "CN"))
            provider_symbol = str(payload.get("provider_symbol", symbol)).strip() or symbol
            themes = [
                str(item).strip()
                for item in payload.get("themes", [])
                if str(item).strip()
            ]
            item: Dict[str, object] = {
                "symbol": symbol,
                "provider_symbol": provider_symbol,
                "name": name,
                "kind": kind,
                "market": market,
            }
            if themes and not is_index:
                item["themes"] = themes
            instruments.append(item)
            raw[list_key] = instruments

            if themes and not is_index:
                theme_stock_map = dict(raw.get("theme_stock_map", {}))
                for theme in themes:
                    symbols = [str(value) for value in theme_stock_map.get(theme, [])]
                    if symbol not in symbols:
                        symbols.append(symbol)
                    theme_stock_map[theme] = symbols
                raw["theme_stock_map"] = theme_stock_map

            Path(self.config_path).write_text(
                json.dumps(raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {
                "added": True,
                "message": f"已添加 {symbol} {name}",
                "state": self.state(),
            }


def build_portfolio_view(portfolio: Portfolio, app: WebApp) -> Dict[str, object]:
    realtime = latest_table_by_symbol(app, "realtime")
    review = latest_table_by_symbol(app, "portfolio")
    positions = []
    total_cost = 0.0
    total_market_value = 0.0
    total_unrealized = 0.0
    total_realized = 0.0
    for position in portfolio.positions:
        realtime_row = realtime.get(position.symbol, {})
        review_row = review.get(position.symbol, {})
        latest_price = first_number(
            realtime_row.get("现价"),
            review_row.get("现价"),
        )
        cost_value = position.cost * position.shares
        market_value = latest_price * position.shares if latest_price else 0.0
        unrealized = (
            (latest_price - position.cost) * position.shares
            if latest_price and position.shares
            else 0.0
        )
        total_cost += cost_value
        total_market_value += market_value
        total_unrealized += unrealized
        total_realized += position.realized_pnl
        positions.append(
            {
                **position.to_dict(),
                "latest_price": latest_price,
                "cost_value": cost_value,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "total_pnl": unrealized + position.realized_pnl,
                "realtime_status": realtime_row.get("状态", ""),
                "realtime_probability": realtime_row.get("当日概率", ""),
                "realtime_advice": realtime_row.get("动作", ""),
                "review_view": review_row.get("短期", ""),
                "review_probability": review_row.get("未来3日概率", ""),
                "review_advice": review_row.get("建议", ""),
            }
        )
    return {
        "cash": portfolio.cash,
        "positions": positions,
        "trades": [trade.to_dict() for trade in reversed(portfolio.trades[-20:])],
        "summary": {
            "position_count": len(portfolio.positions),
            "active_count": len([position for position in portfolio.positions if position.shares > 0]),
            "cost_value": total_cost,
            "market_value": total_market_value,
            "unrealized_pnl": total_unrealized,
            "realized_pnl": total_realized,
            "total_pnl": total_unrealized + total_realized,
        },
    }


def latest_table_by_symbol(app: WebApp, kind: str) -> Dict[str, Dict[str, str]]:
    latest = app.latest_report(kind)
    if not latest:
        return {}
    try:
        markdown = str(app.read_report(str(latest["name"]))["markdown"])
    except (FileNotFoundError, ValueError):
        return {}
    rows = parse_markdown_tables(markdown)
    output: Dict[str, Dict[str, str]] = {}
    for row in rows:
        symbol = row.get("代码", "").strip()
        if symbol and symbol not in output:
            output[symbol] = row
    return output


def parse_markdown_tables(markdown: str) -> List[Dict[str, str]]:
    lines = markdown.splitlines()
    rows: List[Dict[str, str]] = []
    index = 0
    while index < len(lines) - 1:
        line = lines[index]
        next_line = lines[index + 1]
        if line.startswith("|") and is_table_separator(next_line):
            header = split_table_row(line)
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                cells = split_table_row(lines[index])
                row = {
                    header[cell_index]: cells[cell_index]
                    for cell_index in range(min(len(header), len(cells)))
                }
                rows.append(row)
                index += 1
            continue
        index += 1
    return rows


def first_number(*values: object) -> float:
    for value in values:
        text = str(value or "").replace("%", "").replace(",", "").strip()
        if not text or text == "-":
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return 0.0


def create_handler(app: WebApp):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_text(INDEX_HTML, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/state":
                self.send_json(app.state())
                return
            if parsed.path == "/api/reports":
                self.send_json({"reports": app.list_reports()})
                return
            if parsed.path == "/api/history":
                query = parse_qs(parsed.query)
                kind = query.get("kind", [""])[0]
                self.send_json({"reports": app.list_history(kind)})
                return
            if parsed.path == "/api/report":
                query = parse_qs(parsed.query)
                name = query.get("name", [""])[0]
                try:
                    self.send_json(app.read_report(name))
                except FileNotFoundError:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "report not found")
                except ValueError as exc:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if parsed.path == "/api/search":
                query = parse_qs(parsed.query)
                value = query.get("q", [""])[0]
                self.send_json(app.search(value))
                return
            if parsed.path == "/api/portfolio":
                self.send_json(app.portfolio_view())
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {
                "/api/run",
                "/api/instruments",
                "/api/assistant",
                "/api/portfolio/positions",
                "/api/portfolio/trades",
            }:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                body = self.read_json()
                if parsed.path == "/api/run":
                    job = str(body.get("job", ""))
                    self.send_json(app.run_job(job))
                elif parsed.path == "/api/instruments":
                    self.send_json(app.add_instrument(body))
                elif parsed.path == "/api/portfolio/positions":
                    self.send_json(app.add_position(body))
                elif parsed.path == "/api/portfolio/trades":
                    self.send_json(app.record_trade(body))
                else:
                    self.send_json(app.ask_assistant(body))
            except ValueError as exc:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/portfolio/positions":
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            query = parse_qs(parsed.query)
            symbol = query.get("symbol", [""])[0].strip()
            try:
                self.send_json(app.delete_position(symbol))
            except ValueError as exc:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def read_json(self) -> Dict[str, object]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def send_json(self, payload: Dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_error_json(self, status: HTTPStatus, message: str) -> None:
            body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_text(self, text: str, content_type: str) -> None:
            body = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def serve(config_path: str, portfolio_path: Optional[str], host: str, port: int) -> None:
    app = WebApp(config_path=config_path, portfolio_path=portfolio_path)
    server = ThreadingHTTPServer((host, port), create_handler(app))
    print(f"Stock Agent web UI: http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock Agent web UI")
    parser.add_argument("--config", default="config/local.json")
    parser.add_argument("--portfolio", default="data/portfolio.local.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.config, args.portfolio, args.host, args.port)


def report_kind(name: str) -> str:
    for prefix, kind in REPORT_KINDS.items():
        if name.startswith(prefix):
            return kind
    return "other"


def report_rank(report: Dict[str, object]):
    return (1 if report.get("current") else 0, float(report.get("mtime", 0)))


def report_title(path: Path) -> str:
    try:
        return first_heading(path.read_text(encoding="utf-8")) or path.name
    except OSError:
        return path.name


def first_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    html_parts: List[str] = []
    index = 0
    in_list = False

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            index += 1
            continue

        if line.startswith("|") and index + 1 < len(lines) and is_table_separator(lines[index + 1]):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            table_lines = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            html_parts.append(render_table(table_lines))
            continue

        heading_level = heading_marker(line)
        if heading_level:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            text = line[heading_level + 1 :].strip()
            html_parts.append(f"<h{heading_level}>{inline_markdown(text)}</h{heading_level}>")
            index += 1
            continue

        stripped_line = line.lstrip()
        if stripped_line.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{inline_markdown(stripped_line[2:].strip())}</li>")
            index += 1
            continue

        if in_list:
            html_parts.append("</ul>")
            in_list = False
        html_parts.append(f"<p>{inline_markdown(line.strip())}</p>")
        index += 1

    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def heading_marker(line: str) -> int:
    match = re.match(r"^(#{1,4})\s+", line)
    return len(match.group(1)) if match else 0


def is_table_separator(line: str) -> bool:
    return bool(re.match(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", line.strip()))


def render_table(lines: List[str]) -> str:
    rows = [split_table_row(line) for line in lines]
    if len(rows) < 2:
        return ""
    header = rows[0]
    body = rows[2:]
    parts = ["<div class=\"table-wrap\"><table><thead><tr>"]
    parts.extend(f"<th>{inline_markdown(cell)}</th>" for cell in header)
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        parts.extend(f"<td>{inline_markdown(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def split_table_row(line: str) -> List[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}" target="_blank" rel="noreferrer">{match.group(1)}</a>',
        escaped,
    )


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Stock Agent</title>
  <style>
    :root {
      --bg: #f6f7f4;
      --panel: #ffffff;
      --panel-soft: #eef2ef;
      --text: #18211d;
      --muted: #68736d;
      --line: #dfe5e0;
      --green: #138a57;
      --red: #bc3d3a;
      --blue: #2f65b0;
      --amber: #a36516;
      --shadow: 0 8px 24px rgba(23, 38, 30, 0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    button, select {
      font: inherit;
    }

    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
    }

    .sidebar {
      border-right: 1px solid var(--line);
      background: #fbfcfa;
      padding: 18px 14px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }

    .brand {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 18px;
    }

    .brand h1 {
      margin: 0;
      font-size: 19px;
      font-weight: 720;
    }

    .badge {
      border: 1px solid var(--line);
      background: var(--panel-soft);
      color: var(--muted);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
    }

    .nav {
      display: grid;
      gap: 7px;
      margin: 12px 0 18px;
    }

    .nav button,
    .report-row,
    .small-btn {
      border: 1px solid transparent;
      background: transparent;
      color: var(--text);
      border-radius: 7px;
      min-height: 38px;
      text-align: left;
      padding: 9px 10px;
      cursor: pointer;
    }

    .nav button {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .nav button.active,
    .report-row.active {
      border-color: #c8d7cd;
      background: #e8f2ec;
      color: #0d5d39;
      font-weight: 680;
    }

    .meta {
      display: grid;
      gap: 8px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
    }

    .meta div {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 7px;
    }

    .main {
      padding: 18px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 14px;
      min-width: 0;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      min-height: 42px;
    }

    .topbar h2 {
      margin: 0;
      font-size: 22px;
      font-weight: 760;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
      text-align: right;
    }

    .topbar-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }

    .interval-control {
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }

    .interval-control input {
      width: 70px;
      min-height: 30px;
      padding: 5px 8px;
      text-align: right;
    }

    .interval-control.is-hidden {
      display: none;
    }

    .small-btn:hover,
    .report-row:hover,
    .nav button:hover {
      border-color: #b9c9bf;
      background: #edf4ef;
    }

    .primary-btn {
      background: #e8f2ec;
      border-color: #b7cebf;
      color: #0d5d39;
      font-weight: 720;
    }

    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 14px;
      min-height: 0;
    }

    .right-rail {
      display: grid;
      gap: 14px;
      align-content: start;
      min-height: 0;
    }

    .reports,
    .lookup,
    .assistant-panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      min-height: 0;
      box-shadow: var(--shadow);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }

    .lookup {
      align-self: start;
      min-height: 280px;
      max-height: 420px;
    }

    .assistant-panel {
      min-height: 360px;
      max-height: calc(100vh - 560px);
    }

    .assistant-body {
      overflow: auto;
      padding: 12px;
      display: grid;
      grid-template-rows: minmax(120px, 1fr) auto;
      gap: 10px;
    }

    .assistant-thread {
      display: grid;
      align-content: start;
      gap: 8px;
      overflow: auto;
      min-height: 120px;
      padding-right: 2px;
    }

    .assistant-message {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 10px;
      background: #fff;
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
    }

    .assistant-message.user {
      background: #eef6f1;
      border-color: #c8d7cd;
    }

    .assistant-message.assistant {
      background: #fff;
    }

    .assistant-form {
      display: grid;
      gap: 8px;
    }

    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      min-height: 74px;
      resize: vertical;
      padding: 8px 10px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }

    .assistant-form button {
      border: 1px solid #cdd8d0;
      background: #f7faf8;
      color: var(--text);
      border-radius: 7px;
      min-height: 34px;
      cursor: pointer;
      font-weight: 680;
    }

    .assistant-form button:disabled {
      color: var(--muted);
      cursor: not-allowed;
      background: var(--panel-soft);
    }

    .panel-head {
      padding: 12px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }

    .panel-head strong {
      font-size: 14px;
    }

    .small-btn {
      min-height: 30px;
      padding: 5px 9px;
      border-color: var(--line);
      font-size: 12px;
    }

    .report-list {
      overflow: auto;
      padding: 8px;
      display: grid;
      align-content: start;
      gap: 5px;
    }

    .report-row {
      display: grid;
      gap: 2px;
      min-height: 52px;
      border-color: var(--line);
    }

    .report-row span {
      color: var(--muted);
      font-size: 12px;
    }

    .lookup-body {
      overflow: auto;
      padding: 12px;
      display: grid;
      align-content: start;
      gap: 10px;
    }

    .lookup-form,
    .theme-form,
    .position-form,
    .trade-form {
      display: grid;
      gap: 8px;
    }

    input,
    select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      min-height: 38px;
      padding: 8px 10px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }

    .position-dashboard {
      display: grid;
      gap: 16px;
    }

    .position-summary {
      display: grid;
      grid-template-columns: repeat(5, minmax(110px, 1fr));
      gap: 8px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #f8fbf9;
      display: grid;
      gap: 4px;
    }

    .metric span {
      color: var(--muted);
      font-size: 12px;
    }

    .metric strong {
      font-size: 16px;
    }

    .position-tools {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .tool-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      display: grid;
      gap: 10px;
      align-content: start;
      background: #fff;
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .wide-field {
      grid-column: 1 / -1;
    }

    .danger-btn,
    .position-form button,
    .trade-form button {
      border: 1px solid #cdd8d0;
      background: #f7faf8;
      color: var(--text);
      border-radius: 7px;
      min-height: 34px;
      cursor: pointer;
      font-weight: 680;
    }

    .danger-btn {
      border-color: #e2c5c5;
      color: #8c2f2f;
      background: #fff8f8;
      padding: 5px 9px;
      min-height: 30px;
    }

    .position-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .lookup-form button,
    .result-actions button {
      border: 1px solid #cdd8d0;
      background: #f7faf8;
      color: var(--text);
      border-radius: 7px;
      min-height: 34px;
      cursor: pointer;
      font-weight: 680;
    }

    .result-list {
      display: grid;
      gap: 8px;
    }

    .search-row {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      display: grid;
      gap: 8px;
      background: #fff;
    }

    .search-row header {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: flex-start;
    }

    .search-row strong {
      font-size: 14px;
    }

    .search-row span,
    .result-meta {
      color: var(--muted);
      font-size: 12px;
    }

    .result-actions {
      display: grid;
      grid-template-columns: 1fr;
      gap: 7px;
    }

    .result-actions button:disabled {
      color: var(--muted);
      cursor: not-allowed;
      background: var(--panel-soft);
    }

    .viewer {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      overflow: hidden;
    }

    .viewer-title {
      min-width: 0;
    }

    .viewer-title strong,
    .viewer-title span {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .viewer-title span {
      color: var(--muted);
      font-size: 12px;
    }

    .report-html {
      overflow: auto;
      padding: 18px;
    }

    .report-html h1 {
      margin: 0 0 14px;
      font-size: 22px;
    }

    .report-html h2 {
      margin: 22px 0 10px;
      font-size: 17px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 6px;
    }

    .report-html p,
    .report-html li {
      color: #26302a;
    }

    .report-html ul {
      margin: 8px 0 14px;
      padding-left: 20px;
    }

    .table-wrap {
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      margin: 10px 0 16px;
      max-height: min(62vh, 620px);
      position: relative;
    }

    table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      min-width: 1120px;
      background: #fff;
    }

    th,
    td {
      border-bottom: 1px solid var(--line);
      padding: 8px 9px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
      background: #fff;
    }

    th {
      background: #f0f4f1;
      color: #445049;
      font-size: 12px;
      font-weight: 720;
      position: sticky;
      top: 0;
      z-index: 4;
      box-shadow: inset 0 -1px 0 var(--line);
    }

    th:nth-child(1),
    td:nth-child(1) {
      position: sticky;
      left: 0;
      min-width: 88px;
      width: 88px;
      max-width: 88px;
      z-index: 3;
    }

    th:nth-child(2),
    td:nth-child(2) {
      position: sticky;
      left: 88px;
      min-width: 112px;
      width: 112px;
      max-width: 112px;
      z-index: 3;
      box-shadow: 1px 0 0 var(--line);
    }

    th:nth-child(1),
    th:nth-child(2) {
      background: #f0f4f1;
      z-index: 6;
    }

    td:nth-child(1),
    td:nth-child(2) {
      background: #fff;
      font-weight: 560;
    }

    td:last-child {
      white-space: normal;
      min-width: 260px;
    }

    .empty {
      color: var(--muted);
      padding: 18px;
    }

    .loading {
      opacity: 0.65;
      pointer-events: none;
    }

    @media (max-width: 1060px) {
      .shell {
        grid-template-columns: 1fr;
      }

      .sidebar {
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }

      .nav {
        grid-template-columns: repeat(6, minmax(0, 1fr));
      }

      .workspace {
        grid-template-columns: 1fr;
      }

      .position-summary,
      .position-tools {
        grid-template-columns: 1fr;
      }

      .assistant-panel {
        max-height: none;
      }
    }

    @media (max-width: 640px) {
      .main {
        padding: 12px;
      }

      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }

      .nav {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <h1>Stock Agent</h1>
        <span class="badge">local</span>
      </div>
      <nav class="nav" id="nav">
        <button data-kind="realtime" class="active">实时 <span id="count-realtime">0</span></button>
        <button data-kind="review">复盘 <span id="count-review">0</span></button>
        <button data-kind="news">新闻 <span id="count-news">0</span></button>
        <button data-kind="insights">洞察 <span id="count-insights">0</span></button>
        <button data-kind="positions">持仓 <span id="count-positions">0</span></button>
        <button data-kind="portfolio">建议 <span id="count-portfolio">0</span></button>
      </nav>
      <div class="meta">
        <div><span>自选</span><strong id="watch-count">-</strong></div>
        <div><span>指数</span><strong id="index-count">-</strong></div>
        <div><span>持仓</span><strong id="portfolio-count">-</strong></div>
      </div>
    </aside>
    <main class="main">
      <header class="topbar">
        <h2 id="section-title">实时盯盘</h2>
        <div class="topbar-actions">
          <label class="interval-control" id="interval-control">
            <span>刷新间隔</span>
            <input id="refresh-interval" type="number" min="5" step="5" value="30" />
            <span>秒</span>
          </label>
          <div class="status" id="status">准备就绪</div>
        </div>
      </header>
      <section class="workspace">
        <article class="viewer">
          <div class="panel-head">
            <div class="viewer-title">
              <strong id="viewer-heading">未选择报告</strong>
              <span id="viewer-subtitle">当前模块报告；历史归档在 reports/history/</span>
            </div>
            <button class="small-btn primary-btn" id="refresh">立即刷新</button>
          </div>
          <div class="report-html" id="viewer"><div class="empty">暂无内容</div></div>
        </article>
        <aside class="right-rail">
          <section class="lookup">
            <div class="panel-head">
              <strong>标的查询</strong>
              <span class="badge">AKShare</span>
            </div>
            <div class="lookup-body">
              <form class="lookup-form" id="search-form">
                <input id="search-input" placeholder="输入代码或名称，如 300476 / 胜宏 / 恒生" autocomplete="off" />
                <button type="submit">查询</button>
              </form>
              <div class="theme-form">
                <input id="theme-input" placeholder="加入自选时的主题标签，可选：AI, PCB" autocomplete="off" />
              </div>
              <div class="result-list" id="search-results">
                <div class="empty">搜索股票、港股或指数后可直接加入自选/指数池。</div>
              </div>
            </div>
          </section>
          <section class="assistant-panel">
            <div class="panel-head">
              <strong>AI助手</strong>
              <span class="badge" id="assistant-mode">local</span>
            </div>
            <div class="assistant-body">
              <div class="assistant-thread" id="assistant-thread">
                <div class="empty">可以直接问当前页面。</div>
              </div>
              <form class="assistant-form" id="assistant-form">
                <textarea id="assistant-input" placeholder="例如：胜宏科技今天怎么看？" autocomplete="off"></textarea>
                <button type="submit" id="assistant-submit">提问</button>
              </form>
            </div>
          </section>
        </aside>
      </section>
    </main>
  </div>

  <script>
    const routeKinds = ["realtime", "review", "news", "insights", "positions", "portfolio"];
    const initialKind = routeKinds.includes(window.location.hash.slice(1))
      ? window.location.hash.slice(1)
      : "realtime";

    const state = {
      kind: initialKind,
      reports: [],
      activeReport: "",
      searchResults: [],
      activeJob: "",
      runningJobs: 0,
      refreshIntervalSec: 30,
      realtimeTimer: null,
      refreshToken: 0,
      assistantStarted: false
    };

    const titles = {
      realtime: "实时盯盘",
      review: "每日复盘",
      news: "新闻热点",
      insights: "两周洞察",
      positions: "持仓操作",
      portfolio: "持仓建议"
    };

    const statusEl = document.getElementById("status");
    const viewerEl = document.getElementById("viewer");
    const headingEl = document.getElementById("viewer-heading");
    const subtitleEl = document.getElementById("viewer-subtitle");
    const searchForm = document.getElementById("search-form");
    const searchInput = document.getElementById("search-input");
    const themeInput = document.getElementById("theme-input");
    const searchResultsEl = document.getElementById("search-results");
    const refreshIntervalEl = document.getElementById("refresh-interval");
    const intervalControlEl = document.getElementById("interval-control");
    const assistantForm = document.getElementById("assistant-form");
    const assistantInput = document.getElementById("assistant-input");
    const assistantSubmit = document.getElementById("assistant-submit");
    const assistantThreadEl = document.getElementById("assistant-thread");
    const assistantModeEl = document.getElementById("assistant-mode");

    document.querySelectorAll("[data-kind]").forEach(button => {
      button.addEventListener("click", async () => {
        if (state.kind === button.dataset.kind) return;
        state.kind = button.dataset.kind;
        if (window.location.hash.slice(1) !== state.kind) {
          window.history.replaceState(null, "", `#${state.kind}`);
        }
        updateSectionState();
        state.activeReport = "";
        state.refreshToken++;
        try {
          await openLatestOfKind();
        } catch (_) {
          viewerEl.innerHTML = `<div class="empty">正在切换到${escapeHtml(titles[state.kind])}。</div>`;
        }
        refreshCurrentPage("enter");
      });
    });

    document.getElementById("refresh").addEventListener("click", () => refreshCurrentPage("manual"));
    refreshIntervalEl.addEventListener("change", () => {
      state.refreshIntervalSec = normalizeRefreshInterval();
      refreshIntervalEl.value = state.refreshIntervalSec;
      if (state.kind === "realtime") {
        scheduleRealtimeRefresh();
      }
    });
    searchForm.addEventListener("submit", event => {
      event.preventDefault();
      searchInstrument();
    });
    assistantForm.addEventListener("submit", event => {
      event.preventDefault();
      askAssistant();
    });
    viewerEl.addEventListener("submit", event => {
      const form = event.target;
      if (form.id === "position-add-form") {
        event.preventDefault();
        addPortfolioPosition(form);
      }
      if (form.id === "trade-form") {
        event.preventDefault();
        submitPortfolioTrade(form);
      }
    });
    viewerEl.addEventListener("click", event => {
      const button = event.target.closest("[data-delete-position]");
      if (!button) return;
      deletePortfolioPosition(button.dataset.deletePosition);
    });

    async function refreshState() {
      setStatus("刷新中");
      const data = await getJson("/api/state");
      state.reports = data.reports || [];
      document.getElementById("watch-count").textContent = data.watchlist.length;
      document.getElementById("index-count").textContent = data.indices.length;
      document.getElementById("portfolio-count").textContent = data.portfolio_count;
      document.getElementById("count-positions").textContent = data.portfolio_count;
      updateAssistantMode(data.assistant);
      updateCounts();
      if (!state.activeReport) {
        await openLatestOfKind();
      }
      setStatus("准备就绪");
    }

    async function refreshCurrentPage(reason = "enter") {
      if (state.kind === "realtime") {
        scheduleRealtimeRefresh();
      } else if (state.kind === "positions") {
        clearRealtimeRefresh();
        return loadPortfolioPage(reason);
      } else {
        clearRealtimeRefresh();
      }
      return runJob(state.kind, { reason });
    }

    async function runJob(job, options = {}) {
      const reason = options.reason || "manual";
      const targetKind = job === "run_all" ? state.kind : job;
      const isAuto = reason !== "manual";
      if (state.activeJob && reason === "timer") {
        return null;
      }
      if (state.activeJob && reason === "manual") {
        setStatus("已有任务在运行");
        return null;
      }

      const token = ++state.refreshToken;
      state.activeJob = job;
      state.runningJobs += 1;
      if (!isAuto) setBusy(true);
      setStatus(statusTextFor(reason));
      try {
        const data = await postJson("/api/run", { job });
        state.reports = data.all_reports || state.reports;
        updateCounts();
        if (data.reports && data.reports.length && state.kind === targetKind && token === state.refreshToken) {
          const report = data.reports[data.reports.length - 1];
          state.activeReport = report.name;
          displayReport(report);
        }
        setStatus(doneTextFor(reason));
        return data;
      } catch (error) {
        setStatus(error.message || "运行失败");
        try {
          await openLatestOfKind();
        } catch (_) {
          viewerEl.innerHTML = `<div class="empty">${escapeHtml(error.message || "刷新失败")}</div>`;
        }
        return null;
      } finally {
        state.runningJobs = Math.max(0, state.runningJobs - 1);
        if (state.runningJobs === 0 || state.activeJob === job) {
          state.activeJob = "";
        }
        if (!isAuto) setBusy(false);
      }
    }

    async function openReport(name) {
      setStatus("读取报告");
      const report = await getJson(`/api/report?name=${encodeURIComponent(name)}`);
      state.activeReport = report.name;
      displayReport(report);
      setStatus("准备就绪");
    }

    async function openLatestOfKind() {
      const latest = state.reports.find(report => report.kind === state.kind);
      if (latest) {
        await openReport(latest.name);
      } else {
        state.activeReport = "";
        headingEl.textContent = "未选择报告";
        subtitleEl.textContent = "当前模块报告；历史归档在 reports/history/";
        viewerEl.innerHTML = `<div class="empty">暂无${escapeHtml(titles[state.kind])}报告，进入页面后会自动抓取最新数据。</div>`;
      }
    }

    function displayReport(report) {
      if (isStalePremarketRealtimeReport(report)) {
        headingEl.textContent = "实时行情刷新中";
        subtitleEl.textContent = `${report.name} · 旧报告生成于开盘前`;
        viewerEl.innerHTML = `
          <div class="empty">
            当前已经进入交易时间，上一份实时报告是在 09:30 前生成的盘前状态。
            系统正在重新拉取行情，完成后会自动替换为交易中分析。
          </div>
        `;
        return;
      }
      headingEl.textContent = report.title || report.name;
      subtitleEl.textContent = `${report.name} · 历史归档在 reports/history/`;
      viewerEl.innerHTML = report.html || '<div class="empty">暂无内容</div>';
    }

    function isStalePremarketRealtimeReport(report) {
      if (!report || report.kind !== "realtime") return false;
      const markdown = String(report.markdown || "");
      if (!markdown.includes("交易中标的：0") || !markdown.includes("盘前")) return false;
      const match = markdown.match(/生成时间：(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
      if (!match) return false;
      const now = new Date();
      const today = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0")
      ].join("-");
      const reportDate = `${match[1]}-${match[2]}-${match[3]}`;
      const reportMinutes = Number(match[4]) * 60 + Number(match[5]);
      const nowMinutes = now.getHours() * 60 + now.getMinutes();
      return reportDate === today && reportMinutes < 570 && nowMinutes >= 570 && nowMinutes <= 960;
    }

    async function askAssistant() {
      const question = assistantInput.value.trim();
      if (!question) return;
      if (!state.assistantStarted) {
        assistantThreadEl.innerHTML = "";
        state.assistantStarted = true;
      }
      appendAssistantMessage("user", question);
      assistantInput.value = "";
      assistantSubmit.disabled = true;
      setStatus("AI分析中");
      try {
        const data = await postJson("/api/assistant", {
          kind: state.kind,
          report: state.activeReport,
          question
        });
        updateAssistantMode({
          provider: data.provider,
          model: data.model,
          external: data.external
        });
        appendAssistantMessage("assistant", data.answer || "没有生成回答。");
        setStatus("准备就绪");
      } catch (error) {
        appendAssistantMessage("assistant", error.message || "AI助手暂时不可用。");
        setStatus("AI失败");
      } finally {
        assistantSubmit.disabled = false;
      }
    }

    function appendAssistantMessage(role, text) {
      const message = document.createElement("div");
      message.className = `assistant-message ${role}`;
      message.textContent = text;
      assistantThreadEl.appendChild(message);
      assistantThreadEl.scrollTop = assistantThreadEl.scrollHeight;
    }

    function updateAssistantMode(info) {
      if (!info) return;
      const provider = info.provider || "local";
      const model = info.model ? ` · ${info.model}` : "";
      assistantModeEl.textContent = `${provider}${model}`;
    }

    function updateCounts() {
      ["realtime", "review", "news", "insights", "portfolio"].forEach(kind => {
        const count = state.reports.filter(report => report.kind === kind).length;
        document.getElementById(`count-${kind}`).textContent = count;
      });
    }

    function updateSectionState() {
      document.getElementById("section-title").textContent = titles[state.kind];
      document.querySelectorAll("[data-kind]").forEach(item => {
        item.classList.toggle("active", item.dataset.kind === state.kind);
      });
      intervalControlEl.classList.toggle("is-hidden", state.kind !== "realtime");
      document.getElementById("refresh").textContent = state.kind === "realtime" ? "立即刷新" : (state.kind === "positions" ? "刷新建议" : "重新抓取");
    }

    function scheduleRealtimeRefresh() {
      clearRealtimeRefresh();
      state.refreshIntervalSec = normalizeRefreshInterval();
      refreshIntervalEl.value = state.refreshIntervalSec;
      if (state.kind !== "realtime") return;
      state.realtimeTimer = window.setInterval(() => {
        if (state.kind === "realtime") {
          runJob("realtime", { reason: "timer" });
        }
      }, state.refreshIntervalSec * 1000);
    }

    function clearRealtimeRefresh() {
      if (state.realtimeTimer) {
        window.clearInterval(state.realtimeTimer);
        state.realtimeTimer = null;
      }
    }

    function normalizeRefreshInterval() {
      const value = Number(refreshIntervalEl.value || state.refreshIntervalSec || 30);
      if (!Number.isFinite(value)) return 30;
      return Math.max(5, Math.round(value));
    }

    function statusTextFor(reason) {
      if (reason === "timer") return `自动刷新中（${state.refreshIntervalSec}秒）`;
      if (reason === "enter") return "正在抓取最新数据";
      return "正在刷新";
    }

    function doneTextFor(reason) {
      if (reason === "timer") return `已自动刷新 · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
      if (reason === "enter") return "已加载最新数据";
      return "已刷新";
    }

    async function loadPortfolioPage(reason = "enter") {
      if (state.activeJob && reason === "manual") {
        setStatus("已有任务在运行");
        return null;
      }
      state.activeJob = "positions";
      if (reason === "manual") setBusy(true);
      setStatus(reason === "manual" ? "正在刷新持仓建议" : "正在加载持仓");
      try {
        let data = await getJson("/api/portfolio");
        displayPortfolioPage(data);
        document.getElementById("portfolio-count").textContent = data.summary.position_count;
        document.getElementById("count-positions").textContent = data.summary.position_count;

        if (reason === "manual") {
          try {
            const reportData = await postJson("/api/run", { job: "portfolio" });
            state.reports = reportData.all_reports || state.reports;
            updateCounts();
            data = await getJson("/api/portfolio");
            displayPortfolioPage(data);
          } catch (_) {
            // 行情暂不可用时，仍保留已经展示的本地持仓操作台。
          }
        }
        setStatus(reason === "manual" ? "持仓建议已刷新" : "准备就绪");
        return data;
      } catch (error) {
        viewerEl.innerHTML = `<div class="empty">${escapeHtml(error.message || "持仓加载失败")}</div>`;
        setStatus("持仓加载失败");
        return null;
      } finally {
        state.activeJob = "";
        if (reason === "manual") setBusy(false);
      }
    }

    function displayPortfolioPage(data) {
      headingEl.textContent = "持仓操作";
      subtitleEl.textContent = "新增/删除持仓，录入买卖后自动核算成本和盈亏";
      state.activeReport = "";
      const positions = data.positions || [];
      const trades = data.trades || [];
      const summary = data.summary || {};
      const positionOptions = positions.map(position => `
        <option value="${escapeHtml(position.symbol)}">${escapeHtml(position.symbol)} ${escapeHtml(position.name)}</option>
      `).join("");
      const positionRows = positions.length ? positions.map(position => {
        const realtime = [position.realtime_status, position.realtime_probability, position.realtime_advice].filter(Boolean).join(" / ") || "暂无实时建议";
        const review = [position.review_view, position.review_probability, position.review_advice].filter(Boolean).join(" / ") || "暂无复盘建议";
        return `
          <tr>
            <td>${escapeHtml(position.symbol)}</td>
            <td>${escapeHtml(position.name)}</td>
            <td>${formatNumber(position.shares)}</td>
            <td>${formatMoney(position.cost)}</td>
            <td>${formatMoney(position.latest_price)}</td>
            <td>${formatMoney(position.market_value)}</td>
            <td>${formatSignedMoney(position.unrealized_pnl)}</td>
            <td>${formatSignedMoney(position.realized_pnl)}</td>
            <td>${escapeHtml(realtime)}</td>
            <td>${escapeHtml(review)}</td>
            <td><button class="danger-btn" data-delete-position="${escapeHtml(position.symbol)}">删除</button></td>
          </tr>
        `;
      }).join("") : `<tr><td colspan="11">暂无持仓，先在上方新增或直接录入买入交易。</td></tr>`;
      const tradeRows = trades.length ? trades.map(trade => `
        <tr>
          <td>${escapeHtml(trade.timestamp)}</td>
          <td>${escapeHtml(trade.symbol)}</td>
          <td>${trade.side === "buy" ? "买入" : "卖出"}</td>
          <td>${formatNumber(trade.shares)}</td>
          <td>${formatMoney(trade.price)}</td>
          <td>${formatSignedMoney(trade.realized_pnl)}</td>
          <td>${formatMoney(trade.cost_after)}</td>
          <td>${formatNumber(trade.shares_after)}</td>
        </tr>
      `).join("") : `<tr><td colspan="8">暂无交易流水。</td></tr>`;

      viewerEl.innerHTML = `
        <div class="position-dashboard">
          <section class="position-summary">
            ${metricCard("持仓数", formatNumber(summary.position_count || 0))}
            ${metricCard("持仓成本", formatMoney(summary.cost_value))}
            ${metricCard("最新市值", formatMoney(summary.market_value))}
            ${metricCard("浮动盈亏", formatSignedMoney(summary.unrealized_pnl))}
            ${metricCard("总盈亏", formatSignedMoney(summary.total_pnl))}
          </section>
          <section class="position-tools">
            <div class="tool-section">
              <strong>新增持仓</strong>
              <form class="position-form" id="position-add-form">
                <div class="form-grid">
                  <input name="symbol" placeholder="代码，如 300476 / 00700" required />
                  <input name="name" placeholder="名称，如 胜宏科技" />
                  <select name="kind">
                    <option value="a_stock">A股</option>
                    <option value="hk_stock">港股</option>
                  </select>
                  <select name="market">
                    <option value="CN">CN</option>
                    <option value="HK">HK</option>
                  </select>
                  <input name="shares" type="number" min="0" step="1" placeholder="当前股数" />
                  <input name="cost" type="number" min="0" step="0.001" placeholder="当前成本价" />
                  <input name="target_weight" type="number" min="0" step="0.01" placeholder="目标仓位，可选" />
                  <input name="max_loss_pct" type="number" min="0" step="0.01" placeholder="最大亏损，如 0.08" />
                </div>
                <button type="submit">新增持仓</button>
              </form>
              <div class="position-note">也可以不先新增，直接在右侧录入买入交易，系统会自动创建持仓。</div>
            </div>
            <div class="tool-section">
              <strong>买入/卖出录入</strong>
              <form class="trade-form" id="trade-form">
                <div class="form-grid">
                  <input class="wide-field" name="symbol" list="position-symbols" placeholder="持仓代码，如 300476" required />
                  <datalist id="position-symbols">${positionOptions}</datalist>
                  <select name="side">
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                  </select>
                  <input name="price" type="number" min="0" step="0.001" placeholder="成交价" required />
                  <input name="shares" type="number" min="0" step="1" placeholder="成交股数" required />
                  <input name="name" placeholder="名称，新持仓可填" />
                </div>
                <button type="submit">记录交易并重算</button>
              </form>
              <div class="position-note">买入会用加权平均法更新成本；卖出会核算已实现盈亏，剩余持仓成本保持不变。</div>
            </div>
          </section>
          <section>
            <h2>持仓股</h2>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>代码</th><th>名称</th><th>股数</th><th>成本</th><th>现价</th><th>市值</th><th>浮盈</th><th>已实现</th><th>实时建议</th><th>复盘建议</th><th>操作</th>
                  </tr>
                </thead>
                <tbody>${positionRows}</tbody>
              </table>
            </div>
          </section>
          <section>
            <h2>最近交易</h2>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>时间</th><th>代码</th><th>方向</th><th>股数</th><th>价格</th><th>已实现盈亏</th><th>交易后成本</th><th>交易后股数</th>
                  </tr>
                </thead>
                <tbody>${tradeRows}</tbody>
              </table>
            </div>
          </section>
        </div>
      `;
    }

    async function addPortfolioPosition(form) {
      const payload = formPayload(form);
      setBusy(true);
      setStatus("新增持仓中");
      try {
        const data = await postJson("/api/portfolio/positions", payload);
        displayPortfolioPage(data.portfolio);
        setStatus(data.message || "已新增");
      } catch (error) {
        setStatus(error.message || "新增失败");
      } finally {
        setBusy(false);
      }
    }

    async function submitPortfolioTrade(form) {
      const payload = formPayload(form);
      setBusy(true);
      setStatus("记录交易中");
      try {
        const data = await postJson("/api/portfolio/trades", payload);
        displayPortfolioPage(data.portfolio);
        form.reset();
        setStatus(data.message || "交易已记录");
      } catch (error) {
        setStatus(error.message || "交易失败");
      } finally {
        setBusy(false);
      }
    }

    async function deletePortfolioPosition(symbol) {
      if (!symbol) return;
      const ok = window.confirm(`确定删除持仓 ${symbol} 吗？这会修改本地持仓文件。`);
      if (!ok) return;
      setBusy(true);
      setStatus("删除持仓中");
      try {
        const data = await deleteJson(`/api/portfolio/positions?symbol=${encodeURIComponent(symbol)}`);
        displayPortfolioPage(data.portfolio);
        setStatus(data.message || "已删除");
      } catch (error) {
        setStatus(error.message || "删除失败");
      } finally {
        setBusy(false);
      }
    }

    function formPayload(form) {
      const payload = {};
      new FormData(form).forEach((value, key) => {
        payload[key] = String(value).trim();
      });
      return payload;
    }

    function metricCard(label, value) {
      return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }

    function formatMoney(value) {
      const number = Number(value || 0);
      return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function formatSignedMoney(value) {
      const number = Number(value || 0);
      const formatted = formatMoney(Math.abs(number));
      if (number > 0) return `+${formatted}`;
      if (number < 0) return `-${formatted}`;
      return formatted;
    }

    function formatNumber(value) {
      const number = Number(value || 0);
      return number.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
    }

    async function searchInstrument() {
      const query = searchInput.value.trim();
      if (!query) {
        searchResultsEl.innerHTML = '<div class="empty">请输入代码或名称。</div>';
        return;
      }
      setStatus("查询中");
      searchResultsEl.innerHTML = '<div class="empty">正在查询真实代码表和行情数据...</div>';
      try {
        const data = await getJson(`/api/search?q=${encodeURIComponent(query)}`);
        state.searchResults = data.results || [];
        renderSearchResults();
        setStatus("准备就绪");
      } catch (error) {
        searchResultsEl.innerHTML = `<div class="empty">${escapeHtml(error.message || "查询失败")}</div>`;
        setStatus("查询失败");
      }
    }

    function renderSearchResults() {
      searchResultsEl.innerHTML = "";
      if (!state.searchResults.length) {
        searchResultsEl.innerHTML = '<div class="empty">没有找到匹配标的。</div>';
        return;
      }
      state.searchResults.forEach((result, index) => {
        const row = document.createElement("div");
        const price = result.price === null || result.price === undefined ? "-" : Number(result.price).toFixed(3);
        const change = result.change_pct === null || result.change_pct === undefined ? "-" : `${(Number(result.change_pct) * 100).toFixed(2)}%`;
        const actionText = result.kind.endsWith("index") ? "加入指数" : "加入自选";
        row.className = "search-row";
        row.innerHTML = `
          <header>
            <div>
              <strong>${escapeHtml(result.symbol)} ${escapeHtml(result.name)}</strong>
              <div class="result-meta">${escapeHtml(kindLabel(result.kind))} · ${escapeHtml(result.market)} · ${escapeHtml(result.source || "")}</div>
            </div>
            <span>${price} / ${change}</span>
          </header>
          <div class="result-actions">
            <button data-add="${index}" ${result.already_added ? "disabled" : ""}>${result.already_added ? "已存在" : actionText}</button>
          </div>
        `;
        const addButton = row.querySelector("[data-add]");
        if (addButton) {
          addButton.addEventListener("click", () => addInstrument(index));
        }
        searchResultsEl.appendChild(row);
      });
    }

    async function addInstrument(index) {
      const result = state.searchResults[index];
      if (!result) return;
      const themes = themeInput.value
        .split(/[,，、]/)
        .map(item => item.trim())
        .filter(Boolean);
      setBusy(true);
      setStatus("添加中");
      try {
        const payload = { ...result, themes };
        const data = await postJson("/api/instruments", payload);
        if (data.state) {
          state.reports = data.state.reports || state.reports;
          document.getElementById("watch-count").textContent = data.state.watchlist.length;
          document.getElementById("index-count").textContent = data.state.indices.length;
          document.getElementById("portfolio-count").textContent = data.state.portfolio_count;
          updateCounts();
        }
        result.already_added = true;
        renderSearchResults();
        setStatus(data.message || "已添加");
      } catch (error) {
        setStatus(error.message || "添加失败");
      } finally {
        setBusy(false);
      }
    }

    function kindLabel(kind) {
      const labels = {
        a_stock: "A股",
        hk_stock: "港股",
        cn_index: "A股指数",
        hk_index: "港股指数"
      };
      return labels[kind] || kind;
    }

    async function getJson(url) {
      const response = await fetch(url);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "请求失败");
      return data;
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "请求失败");
      return data;
    }

    async function deleteJson(url) {
      const response = await fetch(url, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "请求失败");
      return data;
    }

    function setStatus(text) {
      statusEl.textContent = text;
    }

    function setBusy(isBusy) {
      document.body.classList.toggle("loading", isBusy);
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    async function boot() {
      updateSectionState();
      await refreshState();
      refreshCurrentPage("enter");
    }

    boot();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
