from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def _acc_return_pct(returns_pct: list[float]) -> float:
    if not returns_pct:
        return 0.0
    arr = np.array(returns_pct, dtype=float) / 100.0
    return (np.prod(1 + arr) - 1) * 100


def _max_drawdown_from_returns(returns_pct: list[float]) -> float:
    if not returns_pct:
        return 0.0
    equity = np.cumprod(1 + np.array(returns_pct, dtype=float) / 100.0)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min() * 100)


def compute_portfolio_kpis(monthly_returns: list[float], cdi_returns: dict) -> dict:
    r12 = monthly_returns[-12:]
    r24 = monthly_returns[-24:]
    vol12 = float(np.std(r12, ddof=1) * np.sqrt(12)) if len(r12) > 1 else 0.0
    vol24 = float(np.std(r24, ddof=1) * np.sqrt(12)) if len(r24) > 1 else 0.0
    ret12 = _acc_return_pct(r12)
    sharpe = ((ret12 - cdi_returns.get("12m", 0.0)) / vol12) if vol12 else 0.0
    return {
        "max_drawdown_24m": _max_drawdown_from_returns(r24),
        "sharpe_12m": float(sharpe),
        "vol_12m": vol12,
        "vol_24m": vol24,
        "return_vs_cdi_12m_pp": ret12 - cdi_returns.get("12m", 0.0),
        "return_vs_cdi_12m_pct": (ret12 / cdi_returns.get("12m", 1e-9)) * 100,
        "positive_months_12m": int(sum(x > 0 for x in r12)),
        "negative_months_12m": int(sum(x < 0 for x in r12)),
    }


def compute_asset_kpis(prices: pd.Series, cdi_daily: pd.Series, ref_date: date) -> dict:
    p = prices.sort_index().dropna()
    daily = p.pct_change().dropna()
    end = pd.Timestamp(ref_date)
    p12 = p[p.index >= end - pd.DateOffset(months=12)]
    d12 = daily[daily.index >= end - pd.DateOffset(months=12)]
    vol12 = float(d12.std(ddof=1) * np.sqrt(252) * 100) if len(d12) > 1 else 0.0
    ret12 = float((p12.iloc[-1] / p12.iloc[0] - 1) * 100) if len(p12) > 1 else 0.0

    p24 = p[p.index >= end - pd.DateOffset(months=24)]
    peak = p24.cummax()
    dd = ((p24 - peak) / peak * 100).min() if len(p24) else 0.0

    cdi_12 = ((1 + cdi_daily[cdi_daily.index >= end - pd.DateOffset(months=12)] / 100).prod() - 1) * 100
    sharpe = (ret12 - cdi_12) / vol12 if vol12 else 0.0
    return {
        "max_drawdown_24m": float(dd),
        "sharpe_12m": float(sharpe),
        "vol_12m": vol12,
        "return_12m": ret12,
        "return_vs_cdi_12m_pp": ret12 - cdi_12,
    }
