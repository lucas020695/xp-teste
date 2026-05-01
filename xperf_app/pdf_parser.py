from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
import pymupdf

doc = pymupdf.open(pdf_path)
pages = [page.get_text("text") for page in doc]

import pandas as pd

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

NUM_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}%?")
TICKER_RE = re.compile(r"\b([A-Z]{4}\d{2}[A-Z0-9]?)\b")
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")


def _br_float(raw: str) -> float:
    s = raw.strip().replace("R$", "").replace("%", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    return float(s) if s and s not in {"-", "--"} else 0.0


def _extract_ref_date(text: str, filename: str) -> date | None:
    m = DATE_RE.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").date()
        except ValueError:
            pass

    m = re.search(r"Ref\.(\d{2})\.(\d{2})\.(\d{4})", filename)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    m = re.search(r"Ref\.(\d{2})\.(\d{2})", filename)
    if m:
        return date(2026, int(m.group(2)), int(m.group(1)))

    return None


def _extract_monthly_returns(text: str) -> list[float]:
    vals = []
    for c in re.findall(r"-?\d{1,2},\d{2}%", text):
        try:
            v = _br_float(c)
            if -15 <= v <= 20:
                vals.append(v)
        except Exception:
            pass
    return vals[-24:] if len(vals) > 24 else vals


def _detect_strategy(line: str) -> str | None:
    clean = line.strip().lower()
    for st in STRATEGIES:
        if clean.startswith(st.lower()):
            return st
    return None


def _parse_asset_line(line: str, current_strategy: str | None) -> dict[str, Any] | None:
    nums_raw = NUM_RE.findall(line)
    if len(nums_raw) < 7:
        return None

    nums = [_br_float(x) for x in nums_raw]
    pre = nums[:-6]
    tail = nums[-6:]

    if not pre:
        return None

    saldo = max([x for x in pre if abs(x) >= 100], default=0.0)
    if saldo <= 0:
        return None

    qty_candidates = [x for x in pre if x != saldo]
    qty = qty_candidates[-1] if qty_candidates else 0.0

    first_num_pos = line.find(nums_raw[0])
    name = line[:first_num_pos].strip()
    name = re.sub(r"\s+", " ", name)

    if len(name) < 3:
        return None

    skip = ["relatório", "quantidade", "rentabilidade", "saldo", "carteira", "alocação"]
    if any(s in name.lower() for s in skip):
        return None

    ticker_match = TICKER_RE.search(line)
    ticker = ticker_match.group(1) if ticker_match else ""

    return {
        "strategy": current_strategy or "",
        "name": name[:160],
        "ticker": ticker,
        "qty": qty,
        "saldo_bruto": saldo,
        "alloc_pct": tail[0],
        "rent_mes": tail[1],
        "pct_cdi_mes": tail[2],
        "rent_ano": tail[3],
        "pct_cdi_ano": tail[4],
        "rent_24m": tail[5],
        "pct_cdi_24m": 0.0,
    }


def _extract_text_pymupdf(pdf_path: str) -> tuple[list[str], bool]:
    import fitz

    doc = fitz.open(pdf_path)
    pages = [page.get_text("text") for page in doc]
    avg_chars = sum(len(p or "") for p in pages) / max(len(pages), 1)
    is_digital = avg_chars > 100
    return pages, is_digital


def _extract_text_ocr(pdf_path: str, dpi: int = 250) -> list[str]:
    from pdf2image import convert_from_path
    from pdf2image.exceptions import PDFInfoNotInstalledError
    import pytesseract

    try:
        pages_img = convert_from_path(pdf_path, dpi=dpi)
    except PDFInfoNotInstalledError as e:
        raise RuntimeError(
            "OCR indisponível: o PDF parece escaneado e o Poppler não está instalado no Windows. "
            "Instale o Poppler ou use um PDF com texto nativo."
        ) from e

    result = []
    for img in pages_img:
        txt = pytesseract.image_to_string(img, lang="por+eng", config="--oem 3 --psm 6")
        result.append(txt)
    return result


def extract_pdf(
    pdf_path: str,
    dpi: int = 250,
    first_asset_page: int = 7,
    force_ocr: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    filename = Path(pdf_path).name

    pages, is_digital = _extract_text_pymupdf(pdf_path)
    method = "pymupdf"

    if force_ocr or not is_digital:
        try:
            pages = _extract_text_ocr(pdf_path, dpi=dpi)
            method = "ocr"
            warnings.append("PDF sem texto nativo; OCR utilizado.")
        except RuntimeError as e:
            if not pages:
                raise
            warnings.append(str(e))
            warnings.append("Usando apenas o texto extraído via PyMuPDF, que pode ser parcial.")

    if debug:
        for i, p in enumerate(pages, 1):
            print(f"\n===== PAGE {i} =====\n{p[:2000]}")

    ref_date = None
    for p in pages[:3]:
        ref_date = _extract_ref_date(p or "", filename)
        if ref_date:
            break

    monthly_returns = _extract_monthly_returns("\n".join(pages[:5]))

    rows: list[dict[str, Any]] = []
    current_strategy: str | None = None

    for idx, page_text in enumerate(pages):
        for line in page_text.splitlines():
            st = _detect_strategy(line)
            if st:
                current_strategy = st

        if idx + 1 < first_asset_page:
            continue

        for line in page_text.splitlines():
            st = _detect_strategy(line)
            if st:
                current_strategy = st
                continue

            row = _parse_asset_line(line, current_strategy)
            if row:
                rows.append(row)

    df = pd.DataFrame(rows, columns=ASSET_COLUMNS)
    if not df.empty:
        df = df.drop_duplicates(subset=["name", "saldo_bruto"]).reset_index(drop=True)
        for col in [c for c in ASSET_COLUMNS if c not in {"strategy", "name", "ticker"}]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if df.empty:
        warnings.append("Nenhuma linha de ativo foi detectada.")

    if ref_date is None:
        warnings.append("Data de referência não detectada.")

    return {
        "assets": df,
        "ref_date": ref_date,
        "portfolio_monthly_returns": monthly_returns,
        "parse_warnings": warnings,
        "extraction_method": method,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pdf")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--dpi", type=int, default=250)
    p.add_argument("--page", type=int, default=7)
    p.add_argument("--force-ocr", action="store_true")
    args = p.parse_args()

    result = extract_pdf(
        args.pdf,
        dpi=args.dpi,
        first_asset_page=args.page,
        force_ocr=args.force_ocr,
        debug=args.debug,
    )
    print(
        f"Método={result['extraction_method']} | "
        f"Data={result['ref_date']} | "
        f"Ativos={len(result['assets'])}"
    )
    if result["parse_warnings"]:
        for w in result["parse_warnings"]:
            print("[WARN]", w)


if __name__ == "__main__":
    main()
