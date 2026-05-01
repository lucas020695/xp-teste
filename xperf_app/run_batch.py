from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from data_fetcher import (
    classify_asset_source,
    get_cdi_daily,
    get_cdi_returns,
    get_listed_prices,
    get_tesouro_prices,
    synthetic_return_from_strategy,
)
from kpi_engine import compute_asset_kpis, compute_portfolio_kpis
from pdf_parser import extract_pdf


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python run_batch.py caminho/arquivo.pdf")
        sys.exit(1)
    pdf = sys.argv[1]
    out = Path("output")
    out.mkdir(exist_ok=True)

    parsed = extract_pdf(pdf)
    assets = parsed["assets"]
    ref_date = parsed["ref_date"]

    assets.to_csv(out / "assets_full.csv", index=False)

    tickers = sorted({t for t in assets.get("ticker", pd.Series(dtype=str)).fillna("") if t})
    listed = get_listed_prices(tickers, ref_date)
    cdi_daily = get_cdi_daily(ref_date)
    cdi = get_cdi_returns(ref_date)

    rows = []
    cmp_rows = []
    for _, r in assets.iterrows():
        t = r.get("ticker", "")
        source = classify_asset_source(r.get("name", ""), t)
        ext = None
        if source == "listed" and t in listed:
            ext = listed[t]
        elif source == "tesouro":
            ext = get_tesouro_prices(r.get("name", ""), ref_date)
        else:
            ext = {
                "mes": synthetic_return_from_strategy(r, "mes"),
                "ano": synthetic_return_from_strategy(r, "ano"),
                "12m": synthetic_return_from_strategy(r, "12m"),
                "24m": synthetic_return_from_strategy(r, "24m"),
                "prices": pd.Series([1.0, 1.0], index=[pd.Timestamp(ref_date) - pd.Timedelta(days=1), pd.Timestamp(ref_date)]),
            }

        if ext:
            k = compute_asset_kpis(ext["prices"], cdi_daily, ref_date)
            rows.append({"ticker": t, "source": source, **k})
            cmp_rows.append({
                "ticker": t,
                "source": source,
                "pdf_mes": r.get("rent_mes", 0.0),
                "ext_mes": ext["mes"],
                "delta_mes_pp": ext["mes"] - r.get("rent_mes", 0.0),
                "pdf_ano": r.get("rent_ano", 0.0),
                "ext_ano": ext["ano"],
                **k,
            })

    pd.DataFrame(rows).to_csv(out / "asset_kpis.csv", index=False)
    pd.DataFrame(cmp_rows).to_csv(out / "comparison_pdf_vs_external.csv", index=False)

    pk = compute_portfolio_kpis(parsed["portfolio_monthly_returns"], cdi)
    (out / "portfolio_kpis.json").write_text(json.dumps(pk, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Arquivos gerados em {out.resolve()}")


if __name__ == "__main__":
    main()
