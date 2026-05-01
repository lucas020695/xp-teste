from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytesseract
from pdf2image import convert_from_path

STRATEGIES = [
    "Pós Fixado",
    "Inflação",
    "Pré Fixado",
    "Multimercado",
    "Renda Variável Brasil",
    "Renda Variável Global",
    "Fundos Listados",
    "Alternativo",
    "Caixa",
]

ASSET_COLUMNS = [
    "strategy",
    "name",
    "ticker",
    "qty",
    "saldo_bruto",
    "alloc_pct",
    "rent_mes",
    "pct_cdi_mes",
    "rent_ano",
    "pct_cdi_ano",
    "rent_24m",
    "pct_cdi_24m",
]


@dataclass
class OCRWord:
    text: str
    top: int
    left: int


def _br_to_float(raw: str) -> float:
    clean = raw.replace("R$", "").replace("%", "").replace(" ", "")
    clean = clean.replace(".", "").replace(",", ".")
    if clean in {"", "-", "--"}:
        return 0.0
    return float(clean)


def _extract_ref_date(raw_text: str, filename: str) -> date | None:
    m = re.search(r"(\d{2}/\d{2}/\d{4})", raw_text)
    if m:
        return datetime.strptime(m.group(1), "%d/%m/%Y").date()
    m = re.search(r"Ref\.(\d{2})\.(\d{2})\.(\d{4})", filename)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _group_words_to_lines(data: dict[str, list[Any]], tolerance: int = 8) -> list[str]:
    words: list[OCRWord] = []
    n = len(data["text"])
    for i in range(n):
        txt = str(data["text"][i]).strip()
        if txt:
            words.append(OCRWord(txt, int(data["top"][i]), int(data["left"][i])))
    words.sort(key=lambda w: (w.top, w.left))

    lines: list[list[OCRWord]] = []
    for w in words:
        if not lines:
            lines.append([w])
            continue
        if abs(lines[-1][0].top - w.top) <= tolerance:
            lines[-1].append(w)
        else:
            lines.append([w])

    return [" ".join(x.text for x in sorted(line, key=lambda z: z.left)) for line in lines]


def _extract_monthly_returns(raw_text: str) -> list[float]:
    # from annual historical tables pages 2-5
    cands = re.findall(r"-?\d{1,2},\d{2}%", raw_text)
    vals = []
    for c in cands:
        v = _br_to_float(c)
        if -20 <= v <= 30:
            vals.append(v)
    # keep last 24 unique-ish values as best effort
    if len(vals) > 24:
        vals = vals[-24:]
    return vals


def _parse_asset_line(line: str, current_strategy: str | None) -> dict[str, Any] | None:
    nums = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d{2}%?|-?\d+\.\d+|-?\d+", line)
    if len(nums) < 8:
        return None

    parsed = []
    for n in nums:
        try:
            parsed.append(_br_to_float(n) if ("," in n or "%" in n or "." in n) else float(n))
        except ValueError:
            return None
    if not any(100 <= abs(x) <= 5_000_000 for x in parsed):
        return None

    # capture name before first currency-like token
    split = re.split(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", line, maxsplit=1)
    name = split[0].strip() if split else line
    if not name or name.lower().startswith("relatório"):
        return None

    ticker_match = re.search(r"\b([A-Z]{4}\d{2}[A-Z]?)\b", line)
    ticker = ticker_match.group(1) if ticker_match else ""

    if len(parsed) < 8:
        return None

    tail = parsed[-8:]
    qty = parsed[-9] if len(parsed) >= 9 else 0.0
    saldo = parsed[-10] if len(parsed) >= 10 else parsed[0]

    return {
        "strategy": current_strategy or "",
        "name": name,
        "ticker": ticker,
        "qty": qty,
        "saldo_bruto": saldo,
        "alloc_pct": tail[0],
        "rent_mes": tail[1],
        "pct_cdi_mes": tail[2],
        "rent_ano": tail[3],
        "pct_cdi_ano": tail[4],
        "rent_24m": tail[5],
        "pct_cdi_24m": tail[6],
    }


def extract_pdf(pdf_path: str, dpi: int = 250, first_asset_page: int = 7, debug: bool = False) -> dict[str, Any]:
    pages = convert_from_path(pdf_path, dpi=dpi)
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    current_strategy: str | None = None
    ref_date: date | None = None
    all_text: list[str] = []

    for idx, page in enumerate(pages):
        ocr = pytesseract.image_to_data(page, output_type=pytesseract.Output.DICT, config="--oem 3 --psm 6 -l por+eng")
        lines = _group_words_to_lines(ocr, tolerance=8)
        page_text = "\n".join(lines)
        all_text.append(page_text)

        if debug:
            print(f"\n===== PAGE {idx+1} =====")
            print(page_text)

        if ref_date is None:
            ref_date = _extract_ref_date(page_text, Path(pdf_path).name)

        for st in STRATEGIES:
            if st.lower() in page_text.lower():
                current_strategy = st
                break

        if idx + 1 >= first_asset_page:
            for ln in lines:
                row = _parse_asset_line(ln, current_strategy)
                if row:
                    rows.append(row)

    df = pd.DataFrame(rows, columns=ASSET_COLUMNS).drop_duplicates(subset=["name", "saldo_bruto"])
    monthly_returns = _extract_monthly_returns("\n".join(all_text[:5]))

    if df.empty:
        warnings.append("Nenhuma linha de ativo detectada pelo OCR.")
    if ref_date is None:
        warnings.append("Data de referência não detectada.")

    return {
        "assets": df,
        "ref_date": ref_date,
        "portfolio_monthly_returns": monthly_returns,
        "parse_warnings": warnings,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--dpi", type=int, default=250)
    p.add_argument("--page", type=int, default=7)
    args = p.parse_args()

    result = extract_pdf(args.pdf, dpi=args.dpi, first_asset_page=args.page, debug=args.debug)
    if not args.debug:
        print(result["assets"])


if __name__ == "__main__":
    main()
