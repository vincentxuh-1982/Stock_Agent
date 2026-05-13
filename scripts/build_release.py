from __future__ import annotations

import argparse
import re
import shutil
import stat
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "Stock Agent.app"
SUPPORT_DIR_NAME = "StockAgent"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local macOS release package.")
    parser.add_argument("--version", default=read_version())
    parser.add_argument("--clean", action="store_true", help="Remove dist/ before building.")
    args = parser.parse_args()

    version = args.version.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)?", version):
        raise SystemExit(f"Invalid version: {version}")

    if args.clean and DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(exist_ok=True)

    package_root = DIST / f"StockAgent-{version}-macos"
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    app_bundle = package_root / APP_NAME
    app_resources = app_bundle / "Contents" / "Resources"
    app_code = app_resources / "app"
    make_app_bundle(app_bundle, app_code, version)
    make_package_helpers(package_root, version)

    archive_base = DIST / f"StockAgent-{version}-macos"
    archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=package_root)

    update_root = DIST / f"StockAgent-{version}-update"
    if update_root.exists():
        shutil.rmtree(update_root)
    shutil.copytree(package_root, update_root)
    update_archive = shutil.make_archive(str(update_root), "zip", root_dir=update_root)

    print(f"Release folder: {package_root}")
    print(f"Full package:    {archive_path}")
    print(f"Update package:  {update_archive}")


def read_version() -> str:
    text = (ROOT / "stock_agent" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("Cannot find __version__ in stock_agent/__init__.py")
    return match.group(1)


def make_app_bundle(app_bundle: Path, app_code: Path, version: str) -> None:
    (app_bundle / "Contents" / "MacOS").mkdir(parents=True)
    app_code.mkdir(parents=True)

    copy_source_tree(app_code)
    (app_code / "VERSION").write_text(version + "\n", encoding="utf-8")

    info_plist = app_bundle / "Contents" / "Info.plist"
    info_plist.write_text(info_plist_text(version), encoding="utf-8")

    launcher = app_bundle / "Contents" / "MacOS" / "StockAgent"
    launcher.write_text(launcher_script(), encoding="utf-8")
    make_executable(launcher)


def copy_source_tree(app_code: Path) -> None:
    shutil.copytree(
        ROOT / "stock_agent",
        app_code / "stock_agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    shutil.copytree(
        ROOT / "config",
        app_code / "config",
        ignore=shutil.ignore_patterns(".DS_Store"),
    )
    shutil.copytree(
        ROOT / "data",
        app_code / "data",
        ignore=shutil.ignore_patterns("market", "akshare_cache", ".DS_Store"),
    )
    shutil.copytree(
        ROOT / "scripts",
        app_code / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    shutil.copy2(ROOT / "requirements.txt", app_code / "requirements.txt")
    shutil.copy2(ROOT / "README.md", app_code / "README.md")

    reports_src = ROOT / "reports"
    reports_dst = app_code / "reports"
    reports_dst.mkdir()
    if reports_src.exists():
        for report in reports_src.glob("*_latest.md"):
            shutil.copy2(report, reports_dst / report.name)

    ensure_seed_file(
        app_code / "config" / "local.json",
        app_code / "config" / "china_hk.example.json",
    )
    ensure_seed_file(
        app_code / "data" / "portfolio.local.json",
        app_code / "data" / "portfolio.example.json",
    )


def ensure_seed_file(target: Path, fallback: Path) -> None:
    if target.exists():
        return
    if not fallback.exists():
        raise FileNotFoundError(f"Missing release seed file: {fallback}")
    shutil.copy2(fallback, target)


def make_package_helpers(package_root: Path, version: str) -> None:
    install_command = package_root / "安装或更新.command"
    install_command.write_text(install_or_update_script(), encoding="utf-8")
    make_executable(install_command)

    readme = package_root / "安装说明.md"
    readme.write_text(install_readme(version), encoding="utf-8")


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def info_plist_text(version: str) -> str:
    return dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>CFBundleDevelopmentRegion</key>
          <string>zh_CN</string>
          <key>CFBundleDisplayName</key>
          <string>Stock Agent</string>
          <key>CFBundleExecutable</key>
          <string>StockAgent</string>
          <key>CFBundleIdentifier</key>
          <string>local.stock-agent.app</string>
          <key>CFBundleName</key>
          <string>Stock Agent</string>
          <key>CFBundlePackageType</key>
          <string>APPL</string>
          <key>CFBundleShortVersionString</key>
          <string>{version}</string>
          <key>CFBundleVersion</key>
          <string>{version}</string>
          <key>LSMinimumSystemVersion</key>
          <string>12.0</string>
        </dict>
        </plist>
        """
    )


def launcher_script() -> str:
    return dedent(
        f"""\
        #!/bin/zsh
        set -u

        APP_CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
        APP_CODE="$APP_CONTENTS/Resources/app"
        APP_SUPPORT="${{STOCK_AGENT_HOME:-$HOME/Library/Application Support/{SUPPORT_DIR_NAME}}}"
        CONFIG_DIR="$APP_SUPPORT/config"
        DATA_DIR="$APP_SUPPORT/data"
        REPORT_DIR="$APP_SUPPORT/reports"
        LOG_DIR="$HOME/Library/Logs/{SUPPORT_DIR_NAME}"
        VENV_DIR="$APP_SUPPORT/.venv"
        PORT="${{STOCK_AGENT_PORT:-8765}}"
        URL="http://127.0.0.1:$PORT/#realtime"

        mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$REPORT_DIR/history" "$LOG_DIR"

        copy_if_missing() {{
          local src="$1"
          local dst="$2"
          if [ ! -f "$dst" ] && [ -f "$src" ]; then
            /bin/cp "$src" "$dst"
          fi
        }}

        copy_if_missing "$APP_CODE/config/local.json" "$CONFIG_DIR/local.json"
        copy_if_missing "$APP_CODE/data/portfolio.local.json" "$DATA_DIR/portfolio.local.json"

        if ! /bin/ls "$REPORT_DIR"/*.md >/dev/null 2>&1; then
          for seed_report in "$APP_CODE"/reports/*_latest.md(N); do
            /bin/cp "$seed_report" "$REPORT_DIR/"
          done
        fi

        if /usr/bin/curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
          /usr/bin/open "$URL"
          exit 0
        fi

        if [ ! -x "$VENV_DIR/bin/python" ]; then
          /usr/bin/python3 -m venv "$VENV_DIR"
          "$VENV_DIR/bin/python" -m pip install --upgrade pip >> "$LOG_DIR/bootstrap.log" 2>&1
          "$VENV_DIR/bin/python" -m pip install -r "$APP_CODE/requirements.txt" >> "$LOG_DIR/bootstrap.log" 2>&1
        fi

        export PYTHONPATH="$APP_CODE${{PYTHONPATH:+:$PYTHONPATH}}"
        cd "$APP_SUPPORT"

        "$VENV_DIR/bin/python" -m stock_agent.cli web \\
          --config "$CONFIG_DIR/local.json" \\
          --portfolio "$DATA_DIR/portfolio.local.json" \\
          --host 127.0.0.1 \\
          --port "$PORT" >> "$LOG_DIR/stock-agent.log" 2>&1 &

        SERVER_PID=$!
        for _ in {{1..40}}; do
          if /usr/bin/curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
            /usr/bin/open "$URL"
            break
          fi
          /bin/sleep 0.5
        done

        wait "$SERVER_PID"
        """
    )


def install_or_update_script() -> str:
    return dedent(
        f"""\
        #!/bin/zsh
        set -euo pipefail

        PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
        SOURCE_APP="$PACKAGE_DIR/{APP_NAME}"
        TARGET_DIR="$HOME/Applications"
        TARGET_APP="$TARGET_DIR/{APP_NAME}"
        BACKUP_DIR="$TARGET_DIR/Stock Agent.backup.$(/bin/date +%Y%m%d%H%M%S).app"

        if [ ! -d "$SOURCE_APP" ]; then
          /bin/echo "没有找到 {APP_NAME}，请确认更新包完整。"
          exit 1
        fi

        /bin/mkdir -p "$TARGET_DIR"
        if [ -d "$TARGET_APP" ]; then
          /bin/mv "$TARGET_APP" "$BACKUP_DIR"
          /bin/echo "旧版本已备份到：$BACKUP_DIR"
        fi

        /usr/bin/ditto "$SOURCE_APP" "$TARGET_APP"
        /bin/echo "已安装/更新到：$TARGET_APP"
        /bin/echo "配置、持仓和报告保存在：$HOME/Library/Application Support/{SUPPORT_DIR_NAME}"
        /usr/bin/python3 "$TARGET_APP/Contents/Resources/app/scripts/install_launch_agents.py" \\
          --app "$TARGET_APP" \\
          --no-open
        /usr/bin/open "$TARGET_APP"
        """
    )


def install_readme(version: str) -> str:
    return dedent(
        f"""\
        # Stock Agent {version} 安装说明

        ## 安装或更新

        双击 `安装或更新.command`，程序会安装到：

        `~/Applications/{APP_NAME}`

        安装完成后会自动打开 Stock Agent。本地页面地址：

        `http://127.0.0.1:8765/`

        ## 用户数据位置

        更新 App 不会覆盖你的本地数据。数据保存在：

        `~/Library/Application Support/{SUPPORT_DIR_NAME}/`

        其中：

        - `config/local.json`：指数、自选股、主题标签和调度配置
        - `data/portfolio.local.json`：持仓和交易记录
        - `reports/`：实时、复盘、新闻、洞察和持仓建议报告

        ## 本地后台服务

        安装脚本会注册两个用户级后台服务：

        - `local.stock-agent.web`：本地 Web 服务，登录后自动运行
        - `local.stock-agent.scheduler`：定时报告和微信推送，登录后自动运行

        ## 首次启动

        首次启动会在用户数据目录创建 Python 运行环境，并安装 `requirements.txt` 里的依赖。
        这一步可能需要几分钟，并需要联网下载 AKShare。

        ## 后续更新包

        后续版本继续双击新包里的 `安装或更新.command` 即可。由于用户数据不放在 App 内，更新只替换程序文件。
        安装脚本会同步刷新本地后台服务：Web 服务和调度推送服务都会在登录后自动运行。

        ## 微信推送

        在用户数据目录的 `config/local.json` 里配置 `push` 后，调度器会自动把每日复盘、盘中实时简报和两周洞察推送到微信。
        支持 `wecom`（企业微信群机器人）、`serverchan`（Server 酱）和 `pushplus`。

        ## 日志

        如果页面打不开，可以查看：

        `~/Library/Logs/{SUPPORT_DIR_NAME}/stock-agent.log`

        依赖安装日志在：

        `~/Library/Logs/{SUPPORT_DIR_NAME}/bootstrap.log`
        """
    )


if __name__ == "__main__":
    main()
