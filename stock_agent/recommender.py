from __future__ import annotations

from typing import List

from .models import AnalysisResult, Position


def entry_advice(result: AnalysisResult) -> str:
    metrics = result.metrics
    close = result.close
    support = nearest_support(result)
    resistance = nearest_resistance(result)

    if result.score_short >= 3 and result.score_mid >= 2:
        if metrics["position60"] > 0.82 or metrics["rsi14"] > 72:
            return (
                f"趋势较强但短线位置偏高，优先等待回踩 MA10/MA20 或 {support:.2f} 附近再分批试仓。"
            )
        return (
            f"短中期结构较好，可用小仓位试仓；防守位参考 {support:.2f}，上方压力参考 {resistance:.2f}。"
        )

    if result.score_mid >= 1 and result.score_short <= -1:
        return (
            f"中期结构尚可但短线回落，适合观察缩量止跌；有效跌破 {support:.2f} 前不急于加仓。"
        )

    if result.score_short <= -2 or result.score_mid <= -2:
        return (
            f"趋势偏弱，暂不建议主动建仓；等重新站回 MA20 或放量突破 {resistance:.2f} 再评估。"
        )

    return (
        f"当前信号不够一致，适合放入观察池；区间下沿 {support:.2f}、上沿 {resistance:.2f}。"
    )


def position_advice(result: AnalysisResult, position: Position) -> str:
    close = result.close
    pnl_pct = (close - position.cost) / position.cost if position.cost else 0.0
    hard_stop = position.cost * (1 - position.max_loss_pct) if position.cost else 0.0
    technical_stop = max(nearest_support(result) - result.metrics["atr14"], 0)
    stop = max(hard_stop, technical_stop) if hard_stop else technical_stop

    if close <= stop and result.score_short < 0:
        return (
            f"现价已接近/跌破风控线 {stop:.2f}，且短线转弱，建议优先控制回撤，可减仓或止损。"
        )

    if result.score_short >= 3 and result.score_mid >= 2:
        if pnl_pct > 0.12 and result.metrics["rsi14"] > 72:
            return (
                f"持仓盈利 {pnl_pct:.1%} 且短线偏热，可继续持有但上移止盈线至 {stop:.2f} 附近，避免利润回吐。"
            )
        return (
            f"趋势保持良好，持仓可继续；若回踩不破 {nearest_support(result):.2f}，可按目标仓位小幅加仓。"
        )

    if result.score_mid >= 1 and result.score_short <= -1:
        return (
            f"中期未坏但短线走弱，建议暂停加仓，观察 {nearest_support(result):.2f} 支撑与量能变化。"
        )

    if result.score_mid <= -2:
        return (
            f"中期结构转弱，建议降低仓位；若反抽接近 {nearest_resistance(result):.2f} 但无法放量突破，可继续减仓。"
        )

    return (
        f"信号中性，按原计划持有；风控线参考 {stop:.2f}，突破 {nearest_resistance(result):.2f} 后再考虑提高仓位。"
    )


def nearest_support(result: AnalysisResult) -> float:
    below = [price for price in result.supports if price <= result.close]
    if below:
        return max(below)
    return min(result.supports) if result.supports else result.close


def nearest_resistance(result: AnalysisResult) -> float:
    above = [price for price in result.resistances if price >= result.close]
    if above:
        return min(above)
    return max(result.resistances) if result.resistances else result.close


def summarize_market(results: List[AnalysisResult]) -> str:
    if not results:
        return "未配置指数，无法判断市场环境。"
    avg_short = sum(item.score_short for item in results) / len(results)
    avg_mid = sum(item.score_mid for item in results) / len(results)
    high_risk = sum(1 for item in results if item.risk_level in {"中高", "高"})

    if avg_short >= 2 and avg_mid >= 1:
        mood = "市场环境偏积极，可寻找趋势延续和回踩确认机会。"
    elif avg_short <= -2 or avg_mid <= -1.5:
        mood = "市场环境偏谨慎，优先控制仓位并等待指数企稳。"
    else:
        mood = "市场处于震荡状态，适合轻仓轮动和等待更清晰信号。"

    if high_risk:
        mood += f" 其中 {high_risk} 个指数波动风险偏高。"
    return mood
