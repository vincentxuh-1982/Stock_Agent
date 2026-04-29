from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional

from .config import AgentConfig, NewsSource
from .data_providers import call_with_retries
from .models import Hotspot, NewsItem, StockCandidate


DEFAULT_THEME_KEYWORDS: Dict[str, List[str]] = {
    "AI": ["AI", "人工智能", "大模型", "AIGC", "智能体"],
    "算力": ["算力", "数据中心", "服务器", "GPU", "液冷"],
    "半导体": ["半导体", "芯片", "先进封装", "光刻", "晶圆"],
    "GPU": ["GPU", "GPGPU", "图形处理器", "国产GPU", "AI芯片"],
    "PCB": ["PCB", "印制电路板", "HDI", "覆铜板", "服务器PCB", "高阶板"],
    "机器人": ["机器人", "人形机器人", "自动化", "减速器"],
    "减速器": ["减速器", "齿轮", "精密传动", "谐波减速器"],
    "自动驾驶": ["自动驾驶", "智能驾驶", "Robotaxi", "无人驾驶", "辅助驾驶"],
    "智能驾驶": ["智能驾驶", "自动驾驶", "Robotaxi", "无人驾驶", "辅助驾驶"],
    "新能源": ["新能源", "锂电", "光伏", "储能", "风电"],
    "低空经济": ["低空经济", "eVTOL", "无人机", "通航"],
    "医药": ["医药", "创新药", "医疗器械", "CXO"],
    "消费": ["消费", "白酒", "旅游", "零售", "家电"],
    "有色金属": ["有色", "铜", "铝", "黄金", "稀土", "锂"],
    "银行": ["银行", "息差", "红利", "保险"],
    "券商": ["券商", "证券", "并购重组", "资本市场"],
    "地产": ["地产", "房地产", "城中村", "物业"],
    "军工": ["军工", "航天", "卫星", "航空发动机"],
    "航天": ["航天", "卫星", "航天装备", "商业航天"],
    "通信": ["通信", "光通信", "光模块", "CPO", "数据传输"],
    "新材料": ["新材料", "材料", "碳纤维", "复合材料", "高分子"],
    "汽车": ["汽车", "新能源车", "汽车零部件", "智能座舱", "车企"],
    "汽车电子": ["汽车电子", "车载", "智能座舱", "域控制器", "线控"],
    "汽车零部件": ["汽车零部件", "汽零", "零部件", "汽车供应链"],
    "教育": ["教育", "培训", "职业教育", "企业培训"],
    "企业服务": ["企业服务", "SaaS", "数字化", "管理咨询"],
    "数据要素": ["数据要素", "数据资产", "数据治理", "数据交易"],
    "智能制造": ["智能制造", "工业母机", "精密制造", "减速器", "自动化"],
    "出海": ["出海", "海外订单", "跨境", "关税"],
    "创业板": ["创业板", "创业板指"],
    "港股": ["港股", "港交所", "恒生", "港股通"],
}


def fetch_news(config: AgentConfig, limit_per_source: int = 40) -> List[NewsItem]:
    items: List[NewsItem] = []
    for source in config.news_sources:
        try:
            items.extend(fetch_rss(source, limit=limit_per_source))
        except Exception as exc:
            items.append(
                NewsItem(
                    title=f"{source.name} 新闻源读取失败",
                    link=source.url,
                    source="system",
                    summary=str(exc),
                )
            )
    items.extend(fetch_akshare_market_news(limit=limit_per_source))
    items.extend(fetch_akshare_broad_news(limit=limit_per_source))
    return items


def fetch_akshare_market_news(limit: int = 40) -> List[NewsItem]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []

    items: List[NewsItem] = []
    try:
        frame = ak.stock_news_main_cx()
        for row in frame.head(limit).to_dict("records"):
            summary = str(row.get("summary", "")).strip()
            tag = str(row.get("tag", "市场动态")).strip()
            if not summary:
                continue
            items.append(
                NewsItem(
                    title=summary,
                    link=str(row.get("url", "")),
                    source=f"财新数据通/{tag}",
                    summary=tag,
                )
            )
    except Exception:
        return items
    return items


def fetch_akshare_broad_news(limit: int = 40) -> List[NewsItem]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []

    items: List[NewsItem] = []
    sources = [
        (
            "财联社电报",
            lambda: ak.stock_info_global_cls(symbol="重点"),
            "标题",
            "内容",
            "",
            "发布日期",
            "发布时间",
        ),
        (
            "东方财富快讯",
            ak.stock_info_global_em,
            "标题",
            "摘要",
            "链接",
            "",
            "发布时间",
        ),
        (
            "同花顺财经直播",
            ak.stock_info_global_ths,
            "标题",
            "内容",
            "链接",
            "",
            "发布时间",
        ),
    ]
    per_source = max(6, limit // 3)
    for source_name, loader, title_key, summary_key, link_key, date_key, time_key in sources:
        try:
            frame = call_with_retries(loader, attempts=2)
        except Exception:
            continue
        for row in frame.head(per_source).to_dict("records"):
            title = str(row.get(title_key, "")).strip()
            summary = str(row.get(summary_key, "")).strip()
            if not title and not summary:
                continue
            published_text = " ".join(
                str(row.get(key, "")).strip()
                for key in (date_key, time_key)
                if key and str(row.get(key, "")).strip()
            )
            items.append(
                NewsItem(
                    title=title or summary[:80],
                    link=str(row.get(link_key, "")).strip() if link_key else "",
                    source=source_name,
                    summary=summary,
                    published_at=parse_datetime(published_text),
                )
            )
    return items


def fetch_stock_news(symbol: str, limit: int = 8) -> List[NewsItem]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []

    try:
        frame = call_with_retries(lambda: ak.stock_news_em(symbol=symbol), attempts=2)
    except Exception:
        return []

    items: List[NewsItem] = []
    for row in frame.head(limit).to_dict("records"):
        title = str(row.get("新闻标题", "")).strip()
        summary = str(row.get("新闻内容", "")).strip()
        if not title and not summary:
            continue
        items.append(
            NewsItem(
                title=title or summary[:80],
                link=str(row.get("新闻链接", "")).strip(),
                source=str(row.get("文章来源", "东方财富个股新闻")).strip()
                or "东方财富个股新闻",
                summary=summary,
                published_at=parse_datetime(str(row.get("发布时间", "")).strip()),
            )
        )
    return items


def fetch_hot_candidates(limit: int = 24, keyword_limit: int = 14) -> List[StockCandidate]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []

    candidates: List[StockCandidate] = []
    candidates.extend(fetch_a_hot_candidates(ak, limit=limit, keyword_limit=keyword_limit))
    candidates.extend(fetch_hk_hot_candidates(ak, limit=limit))
    if not candidates:
        candidates.extend(fetch_baidu_hot_candidates(ak, symbol="A股", market="A"))
        candidates.extend(fetch_baidu_hot_candidates(ak, symbol="港股", market="HK"))
    return candidates


def fetch_a_hot_candidates(ak, limit: int, keyword_limit: int) -> List[StockCandidate]:
    candidates: List[StockCandidate] = []
    try:
        frame = call_with_retries(lambda: ak.stock_hot_rank_em(), attempts=2)
    except Exception:
        return candidates

    for index, row in enumerate(frame.head(limit).to_dict("records")):
        code = str(row.get("代码", "")).strip()
        name = str(row.get("股票名称", "")).strip()
        if not code or not name:
            continue
        reason = ""
        if index < keyword_limit:
            reason = hot_keyword_reason(ak, code)
        if not reason:
            reason = "东方财富 A 股人气榜"
        candidates.append(
            StockCandidate(
                symbol=normalize_rank_code(code),
                name=name,
                market="A",
                reason=reason,
                score=int(row.get("当前排名", index + 1) or index + 1),
                change_pct=float(row.get("涨跌幅", 0) or 0),
            )
        )
    return candidates


def fetch_hk_hot_candidates(ak, limit: int) -> List[StockCandidate]:
    candidates: List[StockCandidate] = []
    try:
        frame = call_with_retries(lambda: ak.stock_hk_hot_rank_em(), attempts=2)
    except Exception:
        return candidates

    for index, row in enumerate(frame.head(limit).to_dict("records")):
        code = str(row.get("代码", "")).strip()
        name = str(row.get("股票名称", "")).strip()
        if not code or not name:
            continue
        candidates.append(
            StockCandidate(
                symbol=code.zfill(5),
                name=name,
                market="HK",
                reason="东方财富港股人气榜",
                score=int(row.get("当前排名", index + 1) or index + 1),
                change_pct=float(row.get("涨跌幅", 0) or 0),
            )
        )
    return candidates


def fetch_baidu_hot_candidates(ak, symbol: str, market: str) -> List[StockCandidate]:
    try:
        frame = call_with_retries(
            lambda: ak.stock_hot_search_baidu(
                symbol=symbol,
                date=datetime.now().strftime("%Y%m%d"),
                time="今日",
            ),
            attempts=2,
        )
    except Exception:
        return []

    candidates: List[StockCandidate] = []
    for index, row in enumerate(frame.head(12).to_dict("records")):
        name = str(row.get("名称/代码", "")).strip()
        if not name:
            continue
        candidates.append(
            StockCandidate(
                symbol=name,
                name=name,
                market=market,
                reason=f"百度股市通热搜，综合热度 {row.get('综合热度', '-')}",
                score=index + 1,
                change_pct=parse_percent(row.get("涨跌幅", 0)),
            )
        )
    return candidates


def hot_keyword_reason(ak, code: str) -> str:
    try:
        frame = ak.stock_hot_keyword_em(symbol=code)
    except Exception:
        return ""
    concepts = []
    for row in frame.head(4).to_dict("records"):
        concept = str(row.get("概念名称", "")).strip()
        heat = row.get("热度", "")
        if concept:
            concepts.append(f"{concept}({heat})")
    return "、".join(concepts)


def fetch_rss(source: NewsSource, limit: int = 40) -> List[NewsItem]:
    request = urllib.request.Request(
        source.url,
        headers={"User-Agent": "stock-agent/0.1 (+local research)"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()

    root = ET.fromstring(body)
    channel_items = root.findall(".//item")
    if channel_items:
        return [parse_rss_item(item, source.name) for item in channel_items[:limit]]

    atom_items = root.findall("{http://www.w3.org/2005/Atom}entry")
    return [parse_atom_item(item, source.name) for item in atom_items[:limit]]


def find_hotspots(
    config: AgentConfig,
    items: Iterable[NewsItem],
    candidates: Optional[Iterable[StockCandidate]] = None,
) -> List[Hotspot]:
    by_theme: Dict[str, List[NewsItem]] = defaultdict(list)
    candidates_by_theme: Dict[str, List[StockCandidate]] = defaultdict(list)
    seen_titles = set()
    for item in items:
        title_key = normalize(item.title)
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        haystack = f"{item.title} {item.summary}"
        for theme, keywords in DEFAULT_THEME_KEYWORDS.items():
            if any(keyword.lower() in haystack.lower() for keyword in keywords):
                by_theme[theme].append(item)

    for candidate in candidates or []:
        text = f"{candidate.name} {candidate.reason}"
        matched = themes_for_text(text)
        for theme in matched:
            candidates_by_theme[theme].append(candidate)

    theme_names = sorted(set(by_theme) | set(candidates_by_theme))
    hotspots: List[Hotspot] = []
    for theme in theme_names:
        headlines = by_theme.get(theme, [])
        theme_candidates = dedupe_candidates(candidates_by_theme.get(theme, []))
        related = config.theme_stock_map.get(theme, [])
        hotspots.append(
            Hotspot(
                theme=theme,
                score=len(headlines) + len(theme_candidates),
                headlines=headlines[:6],
                related_symbols=related,
                candidates=theme_candidates[:8],
            )
        )
    return sorted(hotspots, key=lambda item: item.score, reverse=True)


def themes_for_text(value: str) -> List[str]:
    text = value.lower()
    themes: List[str] = []
    for theme, keywords in DEFAULT_THEME_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            themes.append(theme)
    return themes


def dedupe_candidates(candidates: List[StockCandidate]) -> List[StockCandidate]:
    output: List[StockCandidate] = []
    seen = set()
    for candidate in candidates:
        key = f"{candidate.market}:{candidate.symbol}"
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return sorted(output, key=lambda item: item.score)


def parse_rss_item(element: ET.Element, source_name: str) -> NewsItem:
    title = text_of(element, "title")
    link = text_of(element, "link")
    summary = strip_html(text_of(element, "description"))
    published_text = text_of(element, "pubDate")
    return NewsItem(
        title=title,
        link=link,
        source=source_name,
        summary=summary,
        published_at=parse_datetime(published_text),
    )


def parse_atom_item(element: ET.Element, source_name: str) -> NewsItem:
    namespace = "{http://www.w3.org/2005/Atom}"
    title = element.findtext(f"{namespace}title") or ""
    link_element = element.find(f"{namespace}link")
    link = link_element.attrib.get("href", "") if link_element is not None else ""
    summary = strip_html(element.findtext(f"{namespace}summary") or "")
    published_text = element.findtext(f"{namespace}published") or element.findtext(
        f"{namespace}updated"
    )
    return NewsItem(
        title=title.strip(),
        link=link.strip(),
        source=source_name,
        summary=summary,
        published_at=parse_datetime(published_text or ""),
    )


def text_of(element: ET.Element, tag: str) -> str:
    value = element.findtext(tag)
    return (value or "").strip()


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def normalize_rank_code(value: str) -> str:
    return value.upper().replace("SH", "").replace("SZ", "").replace("BJ", "")


def parse_percent(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace("%", "").replace("+", "").strip())
    except ValueError:
        return 0.0


def parse_datetime(value: str):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    for pattern in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None
