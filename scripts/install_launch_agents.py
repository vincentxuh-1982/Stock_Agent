from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "Stock Agent.app"
SUPPORT_DIR_NAME = "StockAgent"
WEB_LABEL = "local.stock-agent.web"
SCHEDULER_LABEL = "local.stock-agent.scheduler"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Stock Agent launchd services.")
    parser.add_argument(
        "--app",
        default=str(Path.home() / "Applications" / APP_NAME),
        help="Path to Stock Agent.app",
    )
    parser.add_argument("--port", default=os.environ.get("STOCK_AGENT_PORT", "8765"))
    parser.add_argument("--skip-bootstrap", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    app_bundle = Path(args.app).expanduser().resolve()
    app_code = app_bundle / "Contents" / "Resources" / "app"
    if not app_code.exists():
        raise SystemExit(f"Cannot find app code: {app_code}")

    support_dir = Path.home() / "Library" / "Application Support" / SUPPORT_DIR_NAME
    config_dir = support_dir / "config"
    data_dir = support_dir / "data"
    report_dir = support_dir / "reports"
    log_dir = Path.home() / "Library" / "Logs" / SUPPORT_DIR_NAME
    venv_dir = support_dir / ".venv"
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"

    for directory in (config_dir, data_dir, report_dir / "history", log_dir, launch_agents_dir):
        directory.mkdir(parents=True, exist_ok=True)

    copy_if_missing(app_code / "config" / "local.json", config_dir / "local.json")
    copy_if_missing(app_code / "data" / "portfolio.local.json", data_dir / "portfolio.local.json")

    if not args.skip_bootstrap:
        ensure_venv(venv_dir, app_code, log_dir)

    python_bin = venv_dir / "bin" / "python"
    if not python_bin.exists():
        python_bin = Path(sys.executable)

    web_plist = launch_agents_dir / f"{WEB_LABEL}.plist"
    scheduler_plist = launch_agents_dir / f"{SCHEDULER_LABEL}.plist"
    write_plist(
        web_plist,
        {
            "Label": WEB_LABEL,
            "ProgramArguments": [
                str(python_bin),
                "-m",
                "stock_agent.cli",
                "web",
                "--config",
                str(config_dir / "local.json"),
                "--portfolio",
                str(data_dir / "portfolio.local.json"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            "WorkingDirectory": str(support_dir),
            "EnvironmentVariables": launch_environment(app_code, support_dir),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_dir / "web.out.log"),
            "StandardErrorPath": str(log_dir / "web.err.log"),
        },
    )
    write_plist(
        scheduler_plist,
        {
            "Label": SCHEDULER_LABEL,
            "ProgramArguments": [
                str(python_bin),
                "-m",
                "stock_agent.cli",
                "schedule",
                "--config",
                str(config_dir / "local.json"),
                "--portfolio",
                str(data_dir / "portfolio.local.json"),
                "--poll-seconds",
                "60",
            ],
            "WorkingDirectory": str(support_dir),
            "EnvironmentVariables": launch_environment(app_code, support_dir),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_dir / "scheduler.out.log"),
            "StandardErrorPath": str(log_dir / "scheduler.err.log"),
        },
    )

    bootstrap_agent(web_plist, WEB_LABEL)
    bootstrap_agent(scheduler_plist, SCHEDULER_LABEL)

    if not args.no_open:
        subprocess.run(["/usr/bin/open", f"http://127.0.0.1:{args.port}/#realtime"], check=False)

    print(f"Web service:       {WEB_LABEL}")
    print(f"Scheduler service: {SCHEDULER_LABEL}")
    print(f"Config:            {config_dir / 'local.json'}")
    print(f"Logs:              {log_dir}")


def copy_if_missing(source: Path, target: Path) -> None:
    if target.exists() or not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def ensure_venv(venv_dir: Path, app_code: Path, log_dir: Path) -> None:
    python_bin = venv_dir / "bin" / "python"
    if not python_bin.exists():
        run_logged(["/usr/bin/python3", "-m", "venv", str(venv_dir)], log_dir / "bootstrap.log")
        run_logged([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"], log_dir / "bootstrap.log")
    run_logged(
        [str(python_bin), "-m", "pip", "install", "-r", str(app_code / "requirements.txt")],
        log_dir / "bootstrap.log",
    )


def run_logged(command: list[str], log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n$ {' '.join(command)}\n")
        subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=True)


def launch_environment(app_code: Path, support_dir: Path) -> dict[str, str]:
    return {
        "PYTHONPATH": str(app_code),
        "STOCK_AGENT_HOME": str(support_dir),
    }


def write_plist(path: Path, payload: dict) -> None:
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)


def bootstrap_agent(plist_path: Path, label: str) -> None:
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain, str(plist_path)], check=False)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist_path)], check=True)
    subprocess.run(["/bin/launchctl", "enable", f"{domain}/{label}"], check=False)
    subprocess.run(["/bin/launchctl", "kickstart", "-k", f"{domain}/{label}"], check=False)


if __name__ == "__main__":
    main()
