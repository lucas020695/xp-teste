# XPerformance Analytics Dashboard

## 1. Pré-requisitos do sistema
- Python 3.11+
- Tesseract OCR (`tesseract --version`)
- Poppler (`pdftoppm -v`)

## 2. Instalação
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Como rodar
Local:
```bash
python app.py
```
EC2/Gunicorn:
```bash
gunicorn -w 2 -b 0.0.0.0:8050 app:app.server
```

## 4. Debug do parser
```bash
python pdf_parser.py arquivo.pdf --debug
python pdf_parser.py arquivo.pdf --dpi 300 --page 7
```

## 5. Fontes de dados
- BCB série 12 (CDI)
- Yahoo Finance com sufixo `.SA`
- CVM INF_DIARIO

## 6. Limitações conhecidas
- OCR pode errar nome dos ativos em alguns PDFs.
- Os números são priorizados com heurísticas e tendem a ficar corretos.
