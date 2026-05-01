"""Busca independente de dados de mercado — 100% gratuito, sem API keys.

Fontes utilizadas:
  - BCB API série 12: CDI diário acumulado
  - Yahoo Finance (yfinance): preços ajustados de tickers .SA (FII, ETF, BDR, ações)
  - CVM INF_DIARIO: cota diária de fundos abertos via CNPJ
  - Tesouro Direto JSON: apenas PU atual (sem histórico via esta fonte)
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import requests
import yfinance as yf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _window_return(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Retorno total (%) entre start e end, usando preço mais próximo disponível."""
    s = series.sort_index().dropna()
    seg = s[(s.index >= start) & (s.index <= end)]
    if len(seg) < 2:
        # tenta pegar o preço anterior a start como referência
        before = s[s.index < start]
        if before.empty or seg.empty:
            return 0.0
        p0 = before.iloc[-1]
        p1 = seg.iloc[-1]
        return (p1 / p0 - 1) * 100.0 if p0 != 0 else 0.0
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100.0


def _returns_dict(series: pd.Series, ref_date: date) -> dict:
    """Calcula retornos para janelas padrão: mes, ano, 12m, 24m."""
    end = pd.Timestamp(ref_date)
    year_start = pd.Timestamp(date(ref_date.year, 1, 1))
    return {
        "mes": _window_return(series, end - pd.DateOffset(months=1), end),
        "ano": _window_return(series, year_start - pd.Timedelta(days=1), end),
        "12m": _window_return(series, end - pd.DateOffset(months=12), end),
        "24m": _window_return(series, end - pd.DateOffset(months=24), end),
        "prices": series,
    }


# ---------------------------------------------------------------------------
# CDI — Banco Central do Brasil
# ---------------------------------------------------------------------------

def get_cdi_daily(ref_date: date, start_date: date | None = None) -> pd.Series:
    """Busca série diária do CDI (BCB série 12).

    Padrão: desde 01/01/2020 até ref_date para cobrir janelas de 24m.
    """
    if start_date is None:
        start_date = date(ref_date.year - 3, 1, 1)
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
    params = {
        "formato": "json",
        "dataInicial": start_date.strftime("%d/%m/%Y"),
        "dataFinal": ref_date.strftime("%d/%m/%Y"),
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["data"] = pd.to_datetime(df["data"], dayfirst=True)
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df.set_index("data")["valor"].sort_index()


def _cdi_acc(cdi_daily: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Acumula CDI diário (%) entre start e end, exclusive start, inclusive end."""
    w = cdi_daily[(cdi_daily.index > start) & (cdi_daily.index <= end)]
    if w.empty:
        return 0.0
    return ((1 + w / 100.0).prod() - 1) * 100.0


def get_cdi_returns(ref_date: date, cdi_daily: pd.Series | None = None) -> dict:
    """Retorna CDI acumulado para as janelas padrão."""
    if cdi_daily is None:
        cdi_daily = get_cdi_daily(ref_date)
    end = pd.Timestamp(ref_date)
    year_start = pd.Timestamp(date(ref_date.year, 1, 1) - timedelta(days=1))
    return {
        "mes": _cdi_acc(cdi_daily, end - pd.DateOffset(months=1), end),
        "ano": _cdi_acc(cdi_daily, year_start, end),
        "12m": _cdi_acc(cdi_daily, end - pd.DateOffset(months=12), end),
        "24m": _cdi_acc(cdi_daily, end - pd.DateOffset(months=24), end),
    }


# ---------------------------------------------------------------------------
# Yahoo Finance — tickers listados na B3 (.SA)
# ---------------------------------------------------------------------------

def get_listed_prices(tickers: list[str], ref_date: date) -> dict:
    """Busca histórico de preços ajustados (inclui dividendos) via Yahoo Finance.

    Retorna dict {ticker: {mes, ano, 12m, 24m, prices}} apenas para tickers
    com dados disponíveis.

    Nota: yfinance usa auto_adjust=True que retroativamente ajusta por splits
    e dividendos. Para FIIs, isso replica bem o retorno total, mas pode diferir
    ±0,3 pp do PDF por timing de ex-data.
    """
    if not tickers:
        return {}

    start = ref_date - timedelta(days=760)  # 24m + margem
    end = ref_date + timedelta(days=5)

    # Baixa em lote para eficiência
    yf_tickers = [f"{t}.SA" for t in tickers]
    raw = yf.download(
        yf_tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    # yfinance retorna MultiIndex quando múltiplos tickers
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": yf_tickers[0]})

    out = {}
    for t in tickers:
        col = f"{t}.SA"
        if col not in close.columns:
            continue
        s = close[col].dropna()
        if s.empty:
            continue
        out[t] = _returns_dict(s, ref_date)

    return out


# ---------------------------------------------------------------------------
# CVM — cota diária de fundos abertos (FIF, FIDC, FIM)
# ---------------------------------------------------------------------------

def get_fund_nav(cnpj: str, ref_date: date, months_back: int = 25) -> dict:
    """Busca histórico de cota via CVM INF_DIARIO.

    Baixa os CSVs mensais do período necessário e monta série temporal.
    Requer CNPJ do fundo (com ou sem formatação).
    """
    cnpj_clean = re.sub(r"\D", "", cnpj)
    frames = []

    # Percorre meses necessários
    cur = date(ref_date.year, ref_date.month, 1)
    for _ in range(months_back):
        ym = cur.strftime("%Y%m")
        url = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ym}.zip"
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                cur = _prev_month(cur)
                continue
            with ZipFile(BytesIO(r.content)) as zf:
                df = pd.read_csv(zf.open(zf.namelist()[0]), sep=";", encoding="latin1")
            col_cnpj = "CNPJ_FUNDO_CLASSE" if "CNPJ_FUNDO_CLASSE" in df.columns else "CNPJ_FUNDO"
            df = df[df[col_cnpj].astype(str).str.replace(r"\D", "", regex=True) == cnpj_clean]
            df["DT_COMPTC"] = pd.to_datetime(df["DT_COMPTC"])
            df["VL_QUOTA"] = pd.to_numeric(df["VL_QUOTA"], errors="coerce")
            frames.append(df[["DT_COMPTC", "VL_QUOTA"]])
        except Exception:
            pass
        cur = _prev_month(cur)
        time.sleep(0.1)  # gentileza com a API CVM

    if not frames:
        return {}

    full = pd.concat(frames).drop_duplicates("DT_COMPTC").set_index("DT_COMPTC")["VL_QUOTA"].dropna().sort_index()
    return _returns_dict(full, ref_date)


def _prev_month(d: date) -> date:
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


# ---------------------------------------------------------------------------
# Classificação de fonte por tipo de ativo
# ---------------------------------------------------------------------------

def classify_asset_source(name: str, ticker: str) -> str:
    """Classifica a melhor fonte de dados para um ativo.

    Returns: 'listed' | 'tesouro' | 'cvm_fund' | 'synthetic'
    """
    u = (name or "").upper()
    t = (ticker or "").upper().strip()

    # Ticker B3: 4 letras + 2 dígitos (ex. BOVA11, AZQA11, BERK34)
    if t and re.match(r"^[A-Z]{4}\d{2}[A-Z0-9]?$", t):
        return "listed"
    if "TESOURO" in u:
        return "tesouro"
    if any(k in u for k in ["AUTOCALL", "BIDIRECIONAL", "TAXA FIXA OU ALTA", "OPÇÃO", "FUTURO"]):
        return "synthetic"
    # Fundos abertos (FIF, FIDC, FIM, FIC)
    fund_keywords = ["FIF", "FIDC", "FIM", "FIC", "FUNDO", "FI ", "FIRF"]
    if any(k in u for k in fund_keywords):
        return "cvm_fund"
    return "synthetic"


def synthetic_return_from_strategy(strategy_row: dict, window: str = "mes") -> float:
    """Proxy para ativos sem série pública: usa retorno do PDF como observado.

    Usado apenas para estruturados e ativos sem fonte gratuita disponível.
    """
    key_map = {"mes": "rent_mes", "ano": "rent_ano", "12m": "rent_24m", "24m": "rent_24m"}
    try:
        return float(strategy_row.get(key_map.get(window, "rent_mes"), 0.0))
    except Exception:
        return 0.0


import re  # noqa: E402 (necessário aqui para classify_asset_source)
