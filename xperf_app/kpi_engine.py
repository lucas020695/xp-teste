"""Motor de KPIs: Max Drawdown, Sharpe, Volatilidade, Retorno vs CDI.

Convenções:
  - Todos os retornos em % (não decimal). Ex: 1.5 = 1,5%.
  - Sharpe anualizado: (retorno_12m - cdi_12m) / (vol_diaria * sqrt(252))
    para ativos com série diária, ou (retorno_12m - cdi_12m) / (vol_mensal * sqrt(12))
    para portfólio com retornos mensais.
  - Max Drawdown: pior queda de pico a vale no período, em %.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _acc_return(returns_pct: list[float] | np.ndarray) -> float:
    """Retorno acumulado a partir de série de retornos percentuais."""
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    return float(np.prod(1.0 + arr) - 1.0) * 100.0


def _max_drawdown(returns_pct: list[float] | np.ndarray) -> float:
    """Max Drawdown (%) a partir de série de retornos periódicos."""
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak * 100.0
    return float(dd.min()) if len(dd) else 0.0


def _max_drawdown_from_prices(prices: pd.Series) -> float:
    """Max Drawdown (%) a partir de série de preços."""
    p = prices.dropna()
    if len(p) < 2:
        return 0.0
    peak = p.cummax()
    dd = (p - peak) / peak * 100.0
    return float(dd.min())


# ---------------------------------------------------------------------------
# KPIs do portfólio (usa retornos mensais extraídos do PDF)
# ---------------------------------------------------------------------------

def compute_portfolio_kpis(monthly_returns: list[float], cdi_returns: dict) -> dict:
    """Calcula KPIs do portfólio consolidado.

    Parameters
    ----------
    monthly_returns : lista de retornos mensais em % (mais recente por último)
    cdi_returns : dict com keys mes, ano, 12m, 24m (de get_cdi_returns)
    """
    r = np.asarray(monthly_returns, dtype=float)
    r12 = r[-12:] if len(r) >= 12 else r
    r24 = r[-24:] if len(r) >= 24 else r

    ret12 = _acc_return(r12)
    ret24 = _acc_return(r24)
    cdi_12 = cdi_returns.get("12m", 0.0)
    cdi_24 = cdi_returns.get("24m", 0.0)

    # Volatilidade mensal anualizada
    vol12_ann = float(np.std(r12, ddof=1) * np.sqrt(12)) if len(r12) > 1 else 0.0
    vol24_ann = float(np.std(r24, ddof=1) * np.sqrt(12)) if len(r24) > 1 else 0.0

    sharpe12 = (ret12 - cdi_12) / vol12_ann if vol12_ann > 0 else 0.0
    sharpe24 = (ret24 - cdi_24) / vol24_ann if vol24_ann > 0 else 0.0

    # Hit rate
    pos12 = int(np.sum(r12 > 0))
    neg12 = int(np.sum(r12 < 0))

    return {
        "return_12m": round(ret12, 4),
        "return_24m": round(ret24, 4),
        "cdi_12m": round(cdi_12, 4),
        "cdi_24m": round(cdi_24, 4),
        "return_vs_cdi_12m_pp": round(ret12 - cdi_12, 4),
        "return_vs_cdi_12m_pct": round((ret12 / cdi_12 * 100) if cdi_12 else 0.0, 2),
        "return_vs_cdi_24m_pp": round(ret24 - cdi_24, 4),
        "max_drawdown_12m": round(_max_drawdown(r12), 4),
        "max_drawdown_24m": round(_max_drawdown(r24), 4),
        "vol_12m_ann": round(vol12_ann, 4),
        "vol_24m_ann": round(vol24_ann, 4),
        "sharpe_12m": round(sharpe12, 4),
        "sharpe_24m": round(sharpe24, 4),
        "positive_months_12m": pos12,
        "negative_months_12m": neg12,
        "hit_rate_12m": round(pos12 / max(len(r12), 1) * 100, 1),
    }


# ---------------------------------------------------------------------------
# KPIs por ativo (usa série de preços diários do Yahoo Finance / CVM)
# ---------------------------------------------------------------------------

def compute_asset_kpis(prices: pd.Series, cdi_daily: pd.Series, ref_date: date) -> dict:
    """KPIs para um ativo com série diária de preços.

    Parameters
    ----------
    prices : série de preços (índice DatetimeIndex)
    cdi_daily : CDI diário em % (série BCB série 12)
    ref_date : data de referência
    """
    p = prices.sort_index().dropna()
    if len(p) < 5:
        return {}

    end = pd.Timestamp(ref_date)
    p12 = p[p.index >= end - pd.DateOffset(months=12)]
    p24 = p[p.index >= end - pd.DateOffset(months=24)]

    # Retornos
    ret12 = float((p12.iloc[-1] / p12.iloc[0] - 1) * 100) if len(p12) > 1 else 0.0
    ret24 = float((p24.iloc[-1] / p24.iloc[0] - 1) * 100) if len(p24) > 1 else 0.0

    # Volatilidade diária anualizada
    daily_ret = p.pct_change().dropna()
    d12 = daily_ret[daily_ret.index >= end - pd.DateOffset(months=12)]
    vol12 = float(d12.std(ddof=1) * np.sqrt(252) * 100) if len(d12) > 2 else 0.0

    # CDI 12m acumulado
    cdi_window = cdi_daily[(cdi_daily.index > end - pd.DateOffset(months=12)) & (cdi_daily.index <= end)]
    cdi_12 = float(((1 + cdi_window / 100).prod() - 1) * 100) if not cdi_window.empty else 0.0

    # Max Drawdown 24m
    mdd24 = _max_drawdown_from_prices(p24)

    sharpe = (ret12 - cdi_12) / vol12 if vol12 > 0 else 0.0

    return {
        "return_12m": round(ret12, 4),
        "return_24m": round(ret24, 4),
        "cdi_12m": round(cdi_12, 4),
        "return_vs_cdi_12m_pp": round(ret12 - cdi_12, 4),
        "max_drawdown_24m": round(mdd24, 4),
        "vol_12m_ann": round(vol12, 4),
        "sharpe_12m": round(sharpe, 4),
    }
