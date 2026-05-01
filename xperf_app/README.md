# XPerformance Analyzer

Analisa relatórios PDF XPerformance da XP Investimentos de forma **totalmente independente**,
comparando retornos com fontes públicas gratuitas: BCB, Yahoo Finance e CVM.

---

## Fontes de Dados (100% gratuitas)

| Fonte | Dados | URL |
|-------|-------|-----|
| BCB API série 12 | CDI diário desde 2000 | api.bcb.gov.br |
| Yahoo Finance (yfinance) | Preços ajustados de tickers B3 | yfinance.download |
| CVM INF_DIARIO | Cota diária de fundos abertos | dados.cvm.gov.br |

---

## Instalação e uso local

```bash
# 1. Clone ou baixe o repositório
git clone https://github.com/lucas020695/xp-teste.git
cd xp-teste

# 2. Crie e ative virtualenv
python -m venv venv
source venv/bin/activate          # Mac/Linux
venv\Scripts\activate              # Windows

# 3. Instale as dependências
pip install -r xperf_app/requirements.txt

# 4. Para PDFs digitais (maioria): não precisa de nada extra
# Para PDFs escaneados (OCR): precisa de poppler e tesseract
#   Windows: https://github.com/oschwartz10612/poppler-windows/releases
#            https://github.com/UB-Mannheim/tesseract/wiki
#   Mac:     brew install poppler tesseract tesseract-lang
#   Linux:   sudo apt install poppler-utils tesseract-ocr tesseract-ocr-por

# 5. Inicie o dashboard
cd xperf_app
python app.py
# Abra http://localhost:8050
```

---

## Deploy no EC2

```bash
# Na sua máquina — envie os arquivos
scp -i sua-chave.pem -r xp-teste ubuntu@<EC2-IP>:~/

# No EC2
cd xp-teste
python3 -m venv venv && source venv/bin/activate
pip install -r xperf_app/requirements.txt

# Instala poppler e tesseract (para suporte OCR)
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-por

# Teste rápido
cd xperf_app && python app.py

# Produção com gunicorn (rode na pasta raiz do repo)
cd ~/xp-teste
gunicorn xperf_app.app:server --bind 0.0.0.0:8050 --workers 2 --timeout 120 &

# Libere a porta 8050 no Security Group da sua instância EC2
# Inbound Rule: TCP 8050, 0.0.0.0/0 (ou seu IP)
```

---

## Modo batch (sem browser)

```bash
cd xperf_app
python run_batch.py caminho/relatorio.pdf
# Gera em ./output:
#   assets_full.csv       — posições completas
#   asset_kpis.csv        — KPIs por ativo
#   portfolio_kpis.json   — Sharpe, MDD, Vol, Return vs CDI
```

---

## Notas sobre discrepâncias PDF vs Independente

- **FIIs listados**: Yahoo Finance ajusta preços retroativamente por dividendos.
  Diferenças de ±0,3 pp são esperadas por timing de ex-data.
- **Fundos abertos** (FIF, FIDC, FIM): precisam do CNPJ para busca precisa via CVM.
  Atualmente aparecem como proxy (retorno do próprio PDF).
- **Estruturados/binários**: sem série pública, usam retorno do PDF como fallback.
- **Títulos privados** (CDB, LCA, CRA, DEB): sem série pública, retorno teórico
  pode ser calculado com a taxa contratada + prazo, mas está fora do escopo atual.
