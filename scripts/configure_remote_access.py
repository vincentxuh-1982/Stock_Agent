#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional


SUPPORT_DIR_NAME = "StockAgent"
TUNNEL_LABEL = "local.stock-agent.tunnel"
DEFAULT_USERNAME = "stock"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enable password-protected remote access for Stock Agent.",
    )
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="Path to the deployed local.json config.",
    )
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default="")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Generate a new password even if one already exists.",
    )
    parser.add_argument("--port", default=os.environ.get("STOCK_AGENT_PORT", "8765"))
    parser.add_argument(
        "--enable-tunnel",
        action="store_true",
        help="Start a Cloudflare quick tunnel through launchd.",
    )
    parser.add_argument("--cloudflared-path", default="")
    parser.add_argument("--no-bootstrap", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    username, password = configure_auth(
        config_path=config_path,
        username=args.username,
        password=args.password,
        reset_password=args.reset_password,
    )

    tunnel_url = ""
    if args.enable_tunnel:
        cloudflared_path = find_cloudflared(args.cloudflared_path)
        log_dir = log_directory()
        log_dir.mkdir(parents=True, exist_ok=True)
        plist_path = write_tunnel_plist(
            cloudflared_path=cloudflared_path,
            port=str(args.port),
            log_dir=log_dir,
        )
        if not args.no_bootstrap:
            bootstrap_agent(plist_path, TUNNEL_LABEL)
            tunnel_url = wait_for_tunnel_url(log_dir)

    print("Stock Agent remote access is configured.")
    print(f"Config:   {config_path}")
    print(f"Username: {username}")
    print(f"Password: {password}")
    if args.enable_tunnel:
        print(f"Tunnel:   {tunnel_url or 'starting; check tunnel.err.log in a few seconds'}")


def default_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / SUPPORT_DIR_NAME
        / "config"
        / "local.json"
    )


def log_directory() -> Path:
    return Path.home() / "Library" / "Logs" / SUPPORT_DIR_NAME


def configure_auth(
    config_path: Path,
    username: str,
    password: str,
    reset_password: bool = False,
) -> tuple[str, str]:
    if not config_path.exists():
        raise SystemExit(f"Cannot find config file: {config_path}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    web = dict(raw.get("web", {}))
    username = username.strip() or str(web.get("username", DEFAULT_USERNAME)).strip()
    username = username or DEFAULT_USERNAME
    existing_password = str(web.get("password", "")).strip()
    password = password.strip()
    if not password and existing_password and not reset_password:
        password = existing_password
    if not password:
        password = secrets.token_urlsafe(18)
    web.update(
        {
            "auth_enabled": True,
            "username": username,
            "password": password,
        }
    )
    raw["web"] = web
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return username, password


def find_cloudflared(explicit_path: str = "") -> str:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.exists():
            return str(path)
        raise SystemExit(f"cloudflared not found: {path}")
    found = shutil.which("cloudflared")
    if found:
        return found
    raise SystemExit(
        "cloudflared is not installed. Install it with: brew install cloudflared"
    )


def write_tunnel_plist(cloudflared_path: str, port: str, log_dir: Path) -> Path:
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents_dir / f"{TUNNEL_LABEL}.plist"
    payload = {
        "Label": TUNNEL_LABEL,
        "ProgramArguments": [
            cloudflared_path,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            "--no-autoupdate",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / "tunnel.out.log"),
        "StandardErrorPath": str(log_dir / "tunnel.err.log"),
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)
    return plist_path


def bootstrap_agent(plist_path: Path, label: str) -> None:
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain, str(plist_path)], check=False)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist_path)], check=True)
    subprocess.run(["/bin/launchctl", "enable", f"{domain}/{label}"], check=False)
    subprocess.run(["/bin/launchctl", "kickstart", "-k", f"{domain}/{label}"], check=False)


def wait_for_tunnel_url(log_dir: Path, timeout_seconds: int = 25) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        url = latest_tunnel_url(log_dir)
        if url:
            return url
        time.sleep(0.5)
    return ""


def latest_tunnel_url(log_dir: Path) -> str:
    pattern = re.compile(r"https://[-a-zA-Z0-9.]+\.trycloudflare\.com")
    matches: list[str] = []
    for path in (log_dir / "tunnel.err.log", log_dir / "tunnel.out.log"):
        if path.exists():
            matches.extend(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return matches[-1] if matches else ""


if __name__ == "__main__":
    main()
