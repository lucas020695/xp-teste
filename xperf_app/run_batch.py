"""Modo batch/CLI — processa PDF sem servidor web e exporta CSVs.

Uso:
    python run_batch.py caminho/para/relatorio.pdf [--output pasta]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from data_fetcher import (
    classify_asset_source,
    get_cdi_daily,
    get_cdi_returns,
    get_listed_prices,
)
from kpi_engine import compute_asset_kpis, compute_portfolio_kpis
from pdf_parser import extract_pdf


def run(pdf_path: str, output_dir: str = "output", force_ocr: bool = False) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] Extraindo PDF: {pdf_path}")
    parsed = extract_pdf(pdf_path, force_ocr=force_ocr)
    df = parsed["assets"]
    ref_date = parsed["ref_date"] or date.today()

    if parsed["parse_warnings"]:
        for w in parsed["parse_warnings"]:
            print(f"  ⚠️  {w}")

    print(f"       Método: {parsed['extraction_method']} | Ativos: {len(df)} | Data ref: {ref_date}")

    # Exporta posições brutas
    df.to_csv(out / "assets_full.csv", index=False)
    print(f"  → assets_full.csv salvo")

    print("[2/5] Buscando CDI (BCB)...")
    cdi_daily = get_cdi_daily(ref_date)
    cdi = get_cdi_returns(ref_date, cdi_daily)
    print(f"       CDI Mês={cdi['mes']:.4f}% | Ano={cdi['ano']:.4f}% | 12M={cdi['12m']:.4f}% | 24M={cdi['24m']:.4f}%")

    print("[3/5] Calculando KPIs do portfólio...")
    monthly = parsed["portfolio_monthly_returns"]
    kpis = compute_portfolio_kpis(monthly, cdi) if monthly else {}
    if kpis:
        print(f"       Sharpe12M={kpis['sharpe_12m']:.2f} | MDD24M={kpis['max_drawdown_24m']:.2f}% "
              f"| Ret12M={kpis['return_12m']:.2f}% | vs CDI={kpis['return_vs_cdi_12m_pp']:+.2f}pp")
        with open(out / "portfolio_kpis.json", "w") as f:
            json.dump(kpis, f, indent=2)
        print("  → portfolio_kpis.json salvo")
    else:
        print("  ⚠️  Retornos mensais insuficientes para calcular KPIs do portfólio.")

    print("[4/5] Buscando preços externos (Yahoo Finance)...")
    tickers = sorted(t for t in df["ticker"].dropna() if str(t).strip())
    ext = get_listed_prices(tickers, ref_date) if tickers else {}
    print(f"       {len(ext)}/{len(tickers)} tickers encontrados")

    print("[5/5] Calculando KPIs por ativo...")
    asset_kpis = []
    for _, row in df.iterrows():
        t = str(row.get("ticker", "")).strip()
        src = classify_asset_source(row.get("name", ""), t)
        rec = {"ticker": t, "name": row["name"], "strategy": row["strategy"], "source": src,
               "pdf_mes": row["rent_mes"], "pdf_ano": row["rent_ano"],
               "saldo_bruto": row["saldo_bruto"]}
        if t in ext:
            pr = ext[t]["prices"]
            ak = compute_asset_kpis(pr, cdi_daily, ref_date)
            rec.update({"ext_mes": ext[t]["mes"], "ext_ano": ext[t]["ano"],
                        "delta_mes": ext[t]["mes"] - row["rent_mes"], **ak})
        asset_kpis.append(rec)

    kpis_df = pd.DataFrame(asset_kpis)
    kpis_df.to_csv(out / "asset_kpis.csv", index=False)
    print(f"  → asset_kpis.csv salvo")

    print(f"\n✅ Concluído. Resultados em: {out.resolve()}")


def main() -> None:
    p = argparse.ArgumentParser(description="XPerf batch processor")
    p.add_argument("pdf", help="Caminho do PDF")
    p.add_argument("--output", default="output", help="Pasta de saída (default: output)")
    p.add_argument("--force-ocr", action="store_true")
    args = p.parse_args()
    run(args.pdf, output_dir=args.output, force_ocr=args.force_ocr)


if __name__ == "__main__":
    main()
