from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import requests
import yfinance as yf


def _window_return(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    s = series.sort_index().dropna()
    w = s[(s.index >= start) & (s.index <= end)]
    if len(w) < 2:
        return 0.0
    return (w.iloc[-1] / w.iloc[0] - 1) * 100


def get_cdi_daily(ref_date: date) -> pd.Series:
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
    params = {"formato": "json", "dataInicial": "01/01/2020", "dataFinal": ref_date.strftime("%d/%m/%Y")}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["data"] = pd.to_datetime(df["data"], dayfirst=True)
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    s = df.set_index("data")["valor"].sort_index()
    return s


def _cdi_acc(cdi_daily: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    w = cdi_daily[(cdi_daily.index > start) & (cdi_daily.index <= end)]
    if w.empty:
        return 0.0
    return ((1 + (w / 100)).prod() - 1) * 100


def get_cdi_returns(ref_date: date) -> dict:
    s = get_cdi_daily(ref_date)
    end = pd.Timestamp(ref_date)
    month_start = (end.replace(day=1) - pd.Timedelta(days=1)).replace(day=1)
    ystart = pd.Timestamp(date(ref_date.year - 1, 12, 31))
    return {
        "mes": _cdi_acc(s, month_start, end),
        "ano": _cdi_acc(s, ystart, end),
        "12m": _cdi_acc(s, end - pd.DateOffset(months=12), end),
        "24m": _cdi_acc(s, end - pd.DateOffset(months=24), end),
    }


def get_listed_prices(tickers: list[str], ref_date: date) -> dict:
    out = {}
    start = ref_date - timedelta(days=730)
    end = ref_date + timedelta(days=5)
    for t in tickers:
        hist = yf.download(f"{t}.SA", start=start, end=end, auto_adjust=True, progress=False)
        if hist.empty:
            continue
        p = hist["Close"].dropna()
        end_ts = pd.Timestamp(ref_date)
        out[t] = {
            "mes": _window_return(p, (end_ts - pd.DateOffset(months=1)), end_ts),
            "ano": _window_return(p, pd.Timestamp(date(ref_date.year, 1, 1)), end_ts),
            "12m": _window_return(p, end_ts - pd.DateOffset(months=12), end_ts),
            "24m": _window_return(p, end_ts - pd.DateOffset(months=24), end_ts),
            "prices": p,
        }
    return out


def get_fund_nav(cnpj: str, ref_date: date) -> dict:
    ym = ref_date.strftime("%Y%m")
    url = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ym}.zip"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with ZipFile(BytesIO(r.content)) as zf:
        name = zf.namelist()[0]
        df = pd.read_csv(zf.open(name), sep=";", encoding="latin1")
    cnpj_clean = cnpj.replace(".", "").replace("/", "").replace("-", "")
    df = df[df["CNPJ_FUNDO_CLASSE"].astype(str).str.replace(r"\D", "", regex=True) == cnpj_clean]
    df["DT_COMPTC"] = pd.to_datetime(df["DT_COMPTC"])
    df["VL_QUOTA"] = pd.to_numeric(df["VL_QUOTA"], errors="coerce")
    p = df.set_index("DT_COMPTC")["VL_QUOTA"].dropna().sort_index()
    end_ts = pd.Timestamp(ref_date)
    return {
        "mes": _window_return(p, end_ts - pd.DateOffset(months=1), end_ts),
        "ano": _window_return(p, pd.Timestamp(date(ref_date.year, 1, 1)), end_ts),
        "12m": _window_return(p, end_ts - pd.DateOffset(months=12), end_ts),
        "24m": _window_return(p, end_ts - pd.DateOffset(months=24), end_ts),
        "prices": p,
    }


def get_tesouro_prices(bond_name: str, ref_date: date) -> dict:
    url = "https://www.tesourodireto.com.br/json/br/com/b3/tesourodireto/bond/search.json"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    matches = [b for b in data.get("response", {}).get("TrsrBdTradgList", []) if bond_name.lower() in b.get("TrsrBd", {}).get("nm", "").lower()]
    if not matches:
        return {}
    pu = float(matches[0]["TrsrBd"]["untrRedVal"])
    s = pd.Series([pu], index=[pd.Timestamp(ref_date)])
    return {"mes": 0.0, "ano": 0.0, "12m": 0.0, "24m": 0.0, "prices": s}



def classify_asset_source(name: str, ticker: str) -> str:
    """Classifica melhor fonte externa possível para o ativo.

    listed: ticker B3/Yahoo (.SA)
    tesouro: títulos Tesouro Direto
    synthetic: estruturados/binários/sem série pública consistente
    """
    u=(name or "").upper()
    t=(ticker or "").upper()
    if t and len(t)>=5 and t[:4].isalpha() and any(ch.isdigit() for ch in t):
        return "listed"
    if "TESOURO" in u:
        return "tesouro"
    if any(k in u for k in ["AUTOCALL", "BIDIRECIONAL", "TAXA FIXA OU ALTA ILIMITADA", "OPÇÃO", "DERIVATIVO", "FUTURO"]):
        return "synthetic"
    return "synthetic"


def synthetic_return_from_strategy(strategy_row: dict, window: str = "mes") -> float:
    """Fallback compatível para ativos sem preço público (ex.: binários/estruturados).

    Usa retorno da própria estratégia no PDF como proxy operacional para comparação.
    """
    key={"mes":"rent_mes","ano":"rent_ano","12m":"rent_24m","24m":"rent_24m"}.get(window,"rent_mes")
    try:
        return float(strategy_row.get(key, 0.0))
    except Exception:
        return 0.0
