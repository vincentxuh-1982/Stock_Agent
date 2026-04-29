from __future__ import annotations

import json
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
    news_sources: List[NewsSource] = field(default_factory=list)
    theme_stock_map: Dict[str, List[str]] = field(default_factory=dict)
    schedules: Dict[str, str] = field(default_factory=dict)

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
