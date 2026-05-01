"""PDF parser usando PyMuPDF (fitz) como primário e pytesseract como fallback OCR.

Estratégia dual:
  1. PyMuPDF extrai texto embutido da maioria dos PDFs XP (rápido, preciso).
  2. Se o texto extraído for insuficiente (PDF escaneado), ativa OCR via pytesseract.

O parser é calibrado para o formato XPerformance da XP Investimentos:
  - Linhas de ativo seguem o padrão:
      <nome> [ticker] <qty> <saldo_bruto> <alloc%> <rent_mes%> <%cdi_mes> <rent_ano%> <%cdi_ano> <rent_24m%> <%cdi_24m>
  - Estratégias são detectadas como cabeçalhos de seção.
  - Retornos mensais do portfólio aparecem nas páginas 2-5 em tabela mensal.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
    "strategy", "name", "ticker", "qty", "saldo_bruto",
    "alloc_pct", "rent_mes", "pct_cdi_mes",
    "rent_ano", "pct_cdi_ano", "rent_24m", "pct_cdi_24m",
]

# Regex para número no formato brasileiro: 1.234,56 ou 1.234,56%
_NUM_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}%?")
_TICKER_RE = re.compile(r"\b([A-Z]{4}\d{2}[A-Z0-9]?)\b")
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_DATE_FILE_RE = re.compile(r"Ref\.(\d{2})\.(\d{2})")


def _br_float(raw: str) -> float:
    """Converte string no formato BR para float."""
    s = raw.strip().replace("R$", "").replace("%", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    return float(s) if s and s not in ("-", "--", "") else 0.0


def _extract_ref_date(text: str, filename: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").date()
        except ValueError:
            pass
    # fallback: nome do arquivo ex. XPerformance-76884-Ref.27.02.pdf
    m2 = _DATE_FILE_RE.search(filename)
    if m2:
        day, month = int(m2.group(1)), int(m2.group(2))
        # assume ano do arquivo
        year = 2026  # default; será sobrescrito se encontrar no texto
        return date(year, month, day)
    return None


def _extract_monthly_returns(text: str) -> list[float]:
    """Extrai retornos mensais do portfólio das tabelas históricas (páginas 2-5).

    O PDF XP apresenta colunas Jan-Dez com valores como '1,47%' ou '-0,84%'.
    Estratégia: coletar todos os valores %-like dentro do intervalo plausível
    e devolver os últimos 24.
    """
    cands = re.findall(r"-?\d{1,2},\d{2}%", text)
    vals = []
    for c in cands:
        try:
            v = _br_float(c)
            if -15.0 <= v <= 20.0:  # range realista para portfolio diversificado
                vals.append(v)
        except ValueError:
            pass
    # Últimos 24 meses (o PDF tem YTD + histórico)
    return vals[-24:] if len(vals) > 24 else vals


def _detect_strategy(line: str) -> str | None:
    """Retorna a estratégia se a linha for um cabeçalho de seção."""
    clean = line.strip()
    for st in STRATEGIES:
        if clean.lower().startswith(st.lower()):
            return st
    return None


def _parse_asset_line(line: str, strategy: str | None) -> dict[str, Any] | None:
    """Tenta extrair um ativo de uma linha de texto.

    Regras heurísticas:
      - A linha deve conter pelo menos 8 números no formato BR.
      - O saldo_bruto é o maior número >= 100 (em R$).
      - Os 6 últimos números (após qty e saldo) são os pares rent/%cdi.
      - Nome é o texto antes do primeiro número grande.
    """
    nums_raw = _NUM_RE.findall(line)
    if len(nums_raw) < 7:
        return None

    try:
        nums = [_br_float(n) for n in nums_raw]
    except ValueError:
        return None

    # Saldo deve ser >= 100 (descarta linhas de rodapé/totalizadores)
    big_idx = next((i for i, v in enumerate(nums) if abs(v) >= 100), None)
    if big_idx is None:
        return None

    # Extrair nome: texto antes do primeiro número grande
    # Localiza posição do primeiro token numérico na linha
    first_num_pos = line.index(nums_raw[big_idx]) if nums_raw[big_idx] in line else 0
    name_raw = line[:first_num_pos].strip()
    # Limpa ruídos OCR comuns
    name_raw = re.sub(r"[^\w\s\-\.\+%/]", "", name_raw).strip()
    if not name_raw or len(name_raw) < 3:
        return None
    # Descarta linhas que são claramente cabeçalhos
    skip_words = ["relatório", "estratégia", "ativo", "quantidade", "saldo", "rentabilidade", "carteira"]
    if any(w in name_raw.lower() for w in skip_words):
        return None

    ticker_m = _TICKER_RE.search(name_raw + " " + line[:40])
    ticker = ticker_m.group(1) if ticker_m else ""

    # Últimos 6 números = rent_mes, pct_cdi_mes, rent_ano, pct_cdi_ano, rent_24m, pct_cdi_24m
    tail = nums[-6:]
    # qty e saldo: podem ser os 1 ou 2 números antes dos 6 finais
    pre = nums[:-6]
    saldo_bruto = max((v for v in pre if abs(v) >= 100), default=0.0)
    qty_candidates = [v for v in pre if v != saldo_bruto]
    qty = qty_candidates[-1] if qty_candidates else 0.0

    # Validação mínima: saldo > 0
    if saldo_bruto <= 0:
        return None

    return {
        "strategy": strategy or "",
        "name": name_raw[:120],
        "ticker": ticker,
        "qty": qty,
        "saldo_bruto": saldo_bruto,
        "alloc_pct": tail[0],
        "rent_mes": tail[1],
        "pct_cdi_mes": tail[2],
        "rent_ano": tail[3],
        "pct_cdi_ano": tail[4],
        "rent_24m": tail[5],
        "pct_cdi_24m": tail[6] if len(tail) > 6 else 0.0,
    }


def _extract_text_pymupdf(pdf_path: str) -> tuple[list[str], bool]:
    """Extrai texto com PyMuPDF. Retorna (páginas, is_digital)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        pages = [page.get_text() for page in doc]
        # Considera digital se total de chars > 500 por página em média
        total_chars = sum(len(p) for p in pages)
        is_digital = (total_chars / max(len(pages), 1)) > 500
        return pages, is_digital
    except ImportError:
        return [], False


def _extract_text_ocr(pdf_path: str, dpi: int = 250) -> list[str]:
    """Fallback OCR via pytesseract + pdf2image."""
    from pdf2image import convert_from_path
    import pytesseract

    pages_img = convert_from_path(pdf_path, dpi=dpi)
    result = []
    for img in pages_img:
        ocr_data = pytesseract.image_to_data(
            img,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 6 -l por+eng",
        )
        words = [
            (str(ocr_data["text"][i]), int(ocr_data["top"][i]), int(ocr_data["left"][i]))
            for i in range(len(ocr_data["text"]))
            if str(ocr_data["text"][i]).strip()
        ]
        words.sort(key=lambda w: (w[1], w[2]))
        # Agrupa em linhas por proximidade vertical
        lines: list[list[str]] = []
        for txt, top, left in words:
            if not lines or abs(top - int(lines[-1][0].split("|")[1])) > 8:
                lines.append([f"{txt}|{top}"])
            else:
                lines[-1].append(f"{txt}|{top}")
        result.append("\n".join(" ".join(w.split("|")[0] for w in line) for line in lines))
    return result


def extract_pdf(
    pdf_path: str,
    dpi: int = 250,
    first_asset_page: int = 7,
    force_ocr: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    """Extrai portfólio, data de referência e retornos mensais do PDF XPerformance.

    Returns
    -------
    dict com keys:
        assets: pd.DataFrame
        ref_date: date | None
        portfolio_monthly_returns: list[float]
        parse_warnings: list[str]
        extraction_method: str  # 'pymupdf' | 'ocr'
    """
    warnings: list[str] = []
    filename = Path(pdf_path).name

    # Tenta PyMuPDF primeiro
    pages, is_digital = _extract_text_pymupdf(pdf_path)
    method = "pymupdf"

    if force_ocr or not is_digital or not pages:
        warnings.append("PDF aparentemente escaneado — usando OCR (mais lento).")
        pages = _extract_text_ocr(pdf_path, dpi=dpi)
        method = "ocr"

    if debug:
        for i, p in enumerate(pages):
            print(f"\n===== PAGE {i+1} =====")
            print(p[:2000])

    # --- Data de referência ---
    ref_date: date | None = None
    for p in pages[:3]:
        ref_date = _extract_ref_date(p, filename)
        if ref_date:
            break
    if not ref_date:
        ref_date = _extract_ref_date("", filename)
    if not ref_date:
        warnings.append("Data de referência não detectada.")

    # --- Retornos mensais do portfólio (páginas 1-5) ---
    monthly_text = "\n".join(pages[:5])
    monthly_returns = _extract_monthly_returns(monthly_text)

    # --- Ativos (a partir da primeira_asset_page) ---
    rows: list[dict[str, Any]] = []
    current_strategy: str | None = None

    for idx, page_text in enumerate(pages):
        # Detecta estratégia em qualquer página (cabeçalhos podem aparecer cedo)
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
        # Garante tipos numéricos
        for col in ["qty", "saldo_bruto", "alloc_pct", "rent_mes", "pct_cdi_mes",
                    "rent_ano", "pct_cdi_ano", "rent_24m", "pct_cdi_24m"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if df.empty:
        warnings.append(
            "Nenhuma linha de ativo detectada. Tente aumentar o DPI (--dpi 300) "
            "ou usar --force-ocr se o PDF for escaneado."
        )

    return {
        "assets": df,
        "ref_date": ref_date,
        "portfolio_monthly_returns": monthly_returns,
        "parse_warnings": warnings,
        "extraction_method": method,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Parser de PDFs XPerformance XP")
    p.add_argument("pdf", help="Caminho para o arquivo PDF")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--dpi", type=int, default=250, help="DPI para OCR (default 250)")
    p.add_argument("--page", type=int, default=7, help="Primeira página com ativos (default 7)")
    p.add_argument("--force-ocr", action="store_true", help="Força OCR mesmo em PDFs digitais")
    args = p.parse_args()

    result = extract_pdf(args.pdf, dpi=args.dpi, first_asset_page=args.page,
                         force_ocr=args.force_ocr, debug=args.debug)

    if result["parse_warnings"]:
        for w in result["parse_warnings"]:
            print(f"[AVISO] {w}")

    print(f"Método: {result['extraction_method']} | Data ref: {result['ref_date']} | "
          f"Ativos: {len(result['assets'])} | Retornos mensais: {len(result['portfolio_monthly_returns'])}")
    if not args.debug:
        print(result["assets"].to_string())


if __name__ == "__main__":
    main()
