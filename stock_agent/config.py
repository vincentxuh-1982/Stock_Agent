from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .models import Instrument


@dataclass
class NewsSource:
    name: str
    url: str

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "NewsSource":
        return cls(name=str(data["name"]), url=str(data["url"]))


@dataclass
class PushConfig:
    provider: str = "disabled"
    wechat_webhook_url: str = ""
    server_chan_send_key: str = ""
    pushplus_token: str = ""
    enabled: bool = False
    max_chars: int = 12000

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "PushConfig":
        provider = str(data.get("provider", "disabled")).strip().lower() or "disabled"
        return cls(
            provider=provider,
            wechat_webhook_url=str(data.get("wechat_webhook_url", "")).strip(),
            server_chan_send_key=str(data.get("server_chan_send_key", "")).strip(),
            pushplus_token=str(data.get("pushplus_token", "")).strip(),
            enabled=bool(data.get("enabled", provider != "disabled")),
            max_chars=int(data.get("max_chars", 12000) or 12000),
        )

    def with_environment(self) -> "PushConfig":
        provider = os.environ.get("STOCK_AGENT_PUSH_PROVIDER", self.provider).strip().lower()
        wechat_webhook_url = os.environ.get(
            "STOCK_AGENT_WECHAT_WEBHOOK_URL",
            self.wechat_webhook_url,
        ).strip()
        server_chan_send_key = os.environ.get(
            "STOCK_AGENT_SERVERCHAN_SEND_KEY",
            self.server_chan_send_key,
        ).strip()
        pushplus_token = os.environ.get(
            "STOCK_AGENT_PUSHPLUS_TOKEN",
            self.pushplus_token,
        ).strip()
        enabled_value = os.environ.get("STOCK_AGENT_PUSH_ENABLED", "")
        if enabled_value:
            enabled = enabled_value.strip().lower() in {"1", "true", "yes", "on"}
        else:
            enabled = self.enabled or bool(
                wechat_webhook_url or server_chan_send_key or pushplus_token
            )
        return PushConfig(
            provider=provider or "disabled",
            wechat_webhook_url=wechat_webhook_url,
            server_chan_send_key=server_chan_send_key,
            pushplus_token=pushplus_token,
            enabled=enabled,
            max_chars=self.max_chars,
        )


@dataclass
class WebConfig:
    auth_enabled: bool = False
    username: str = "stock"
    password: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "WebConfig":
        enabled_value = data.get("auth_enabled", data.get("enabled", False))
        return cls(
            auth_enabled=bool(enabled_value),
            username=str(data.get("username", "stock")).strip() or "stock",
            password=str(data.get("password", "")).strip(),
        )

    def with_environment(self) -> "WebConfig":
        enabled_value = os.environ.get("STOCK_AGENT_WEB_AUTH_ENABLED", "")
        if enabled_value:
            auth_enabled = enabled_value.strip().lower() in {"1", "true", "yes", "on"}
        else:
            auth_enabled = self.auth_enabled
        return WebConfig(
            auth_enabled=auth_enabled,
            username=os.environ.get("STOCK_AGENT_WEB_USERNAME", self.username).strip()
            or "stock",
            password=os.environ.get("STOCK_AGENT_WEB_PASSWORD", self.password).strip(),
        )


@dataclass
class AgentConfig:
    market: str = "CN"
    timezone: str = "Asia/Shanghai"
    data_provider: str = "synthetic"
    data_dir: str = "data/market"
    output_dir: str = "reports"
    lookback_days: int = 180
    adjust: str = "qfq"
    indices: List[Instrument] = field(default_factory=list)
    watchlist: List[Instrument] = field(default_factory=list)
    etf_pools: Dict[str, List[Instrument]] = field(default_factory=dict)
    news_sources: List[NewsSource] = field(default_factory=list)
    theme_stock_map: Dict[str, List[str]] = field(default_factory=dict)
    schedules: Dict[str, str] = field(default_factory=dict)
    push: PushConfig = field(default_factory=PushConfig)
    web: WebConfig = field(default_factory=WebConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "AgentConfig":
        return cls(
            market=str(data.get("market", "CN")),
            timezone=str(data.get("timezone", "Asia/Shanghai")),
            data_provider=str(data.get("data_provider", "synthetic")),
            data_dir=str(data.get("data_dir", "data/market")),
            output_dir=str(data.get("output_dir", "reports")),
            lookback_days=int(data.get("lookback_days", 180) or 180),
            adjust=str(data.get("adjust", "qfq")),
            indices=[Instrument.from_dict(x) for x in data.get("indices", [])],
            watchlist=[Instrument.from_dict(x) for x in data.get("watchlist", [])],
            etf_pools={
                str(pool): [Instrument.from_dict(item) for item in instruments]
                for pool, instruments in dict(data.get("etf_pools", {})).items()
            },
            news_sources=[
                NewsSource.from_dict(x) for x in data.get("news_sources", [])
            ],
            theme_stock_map={
                str(k): [str(symbol) for symbol in v]
                for k, v in dict(data.get("theme_stock_map", {})).items()
            },
            schedules={
                str(k): str(v) for k, v in dict(data.get("schedules", {})).items()
            },
            push=PushConfig.from_dict(dict(data.get("push", {}))).with_environment(),
            web=WebConfig.from_dict(dict(data.get("web", {}))).with_environment(),
        )


def load_config(path: str) -> AgentConfig:
    raw = load_mapping(path)
    return AgentConfig.from_dict(raw)


def load_mapping(path: str) -> Dict[str, object]:
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")

    if config_path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "YAML config requires PyYAML. Use JSON config or install PyYAML."
            ) from exc
        return yaml.safe_load(text) or {}

    return json.loads(text)


def project_path(path: str, base: Optional[Path] = None) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    return (base or Path.cwd()) / target
