from __future__ import annotations

import base64
import io
import tempfile

import dash
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, State, dcc, html, dash_table

from data_fetcher import classify_asset_source, get_listed_prices, synthetic_return_from_strategy
from pdf_parser import extract_pdf

COLORS = {'bg':'#f7f6f2','surface':'#f9f8f5','surface2':'#fbfbf9','border':'#d4d1ca','text':'#28251d','text_muted':'#7a7974','primary':'#01696f','primary_hover':'#0c4e54','success':'#437a22','warning':'#964219','error':'#a12c7b','divider':'#dcd9d5'}
CARD_STYLE = {'background': COLORS['surface'],'border': f"1px solid {COLORS['border']}",'borderRadius': '8px','padding': '20px','boxShadow': '0 1px 3px rgba(40,37,29,0.06)'}
KPI_CARD_STYLE = {**CARD_STYLE,'textAlign': 'center','minWidth': '160px'}

app = Dash(__name__)
app.layout = html.Div(style={"background": COLORS["bg"], "padding": "16px"}, children=[
    dcc.Store(id="store-data"), dcc.Store(id="store-comp"),
    dcc.Tabs(id="tabs", value="upload", children=[
        dcc.Tab(label="📤 Upload", value="upload", children=[
            dcc.Upload(id="upload-pdf", children=html.Button("Enviar PDF", style={"background": COLORS["primary"], "color": "white"})),
            dcc.Loading(html.Div(id="upload-status"))
        ]),
        dcc.Tab(label="📋 Portfólio", value="portfolio", children=[html.Div(id="portfolio-view")]),
        dcc.Tab(label="📊 KPIs", value="kpis", children=[html.Div(id="kpis-view")]),
        dcc.Tab(label="📸 Snapshot / Discrepâncias", value="snap", children=[html.Button("🔄 Buscar Dados Externos", id="btn-fetch"), dcc.Loading(html.Div(id="snapshot-view"))]),
    ])
])

@app.callback(Output("store-data", "data"), Output("upload-status", "children"), Input("upload-pdf", "contents"), State("upload-pdf", "filename"), prevent_initial_call=True)
def upload(contents, filename):
    content = base64.b64decode(contents.split(",", 1)[1])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as f:
        f.write(content); f.flush()
        parsed = extract_pdf(f.name)
    assets = parsed["assets"]
    payload = {"assets": assets.to_dict("records"), "ref_date": str(parsed["ref_date"]), "monthly_returns": parsed["portfolio_monthly_returns"], "warnings": parsed["parse_warnings"]}
    return payload, f"Data: {parsed['ref_date']} | Ativos: {len(assets)} | Saldo total: R$ {assets['saldo_bruto'].sum():,.2f}"

@app.callback(Output("portfolio-view", "children"), Input("store-data", "data"))
def render_portfolio(data):
    if not data:
        return "Faça upload."
    df = pd.DataFrame(data["assets"])
    pie = px.pie(df, names="strategy", values="saldo_bruto", hole=0.5)
    return [dcc.Graph(figure=pie), dash_table.DataTable(data=df.to_dict("records"), columns=[{"name": c, "id": c} for c in df.columns], style_table={"overflowX": "auto"})]

@app.callback(Output("kpis-view", "children"), Input("store-data", "data"))
def render_kpis(data):
    if not data:
        return "Faça upload."
    r = pd.Series(data["monthly_returns"])
    curve = (1 + r / 100).cumprod()
    return dcc.Graph(figure=px.line(x=list(range(len(curve))), y=curve))

@app.callback(Output("store-comp", "data"), Input("btn-fetch", "n_clicks"), State("store-data", "data"), prevent_initial_call=True)
def fetch_external(_, data):
    df = pd.DataFrame(data["assets"])
    tickers = sorted(set(df["ticker"].dropna()) - {""})
    ext = get_listed_prices(tickers, pd.to_datetime(data["ref_date"]).date())
    rows = []
    for _, r in df.iterrows():
        t = r["ticker"]
        src = classify_asset_source(r.get("name", ""), t)
        if t in ext:
            em, ea = ext[t]["mes"], ext[t]["ano"]
        else:
            em, ea = synthetic_return_from_strategy(r, "mes"), synthetic_return_from_strategy(r, "ano")
            src = f"{src}-proxy"
        rows.append({"ticker": t, "source": src, "pdf_mes": r["rent_mes"], "ext_mes": em, "delta": em - r["rent_mes"], "pdf_ano": r["rent_ano"], "ext_ano": ea})
    return rows

@app.callback(Output("snapshot-view", "children"), Input("store-comp", "data"))
def render_snapshot(comp):
    if not comp:
        return "Sem comparação."
    c = pd.DataFrame(comp)
    wf = px.bar(c.sort_values("delta", key=abs), x="ticker", y="delta", color="delta", color_continuous_scale=["red", "green"])
    sc = px.scatter(c, x="pdf_mes", y="ext_mes", hover_data=["ticker", "delta"])
    return [dcc.Graph(figure=wf), dcc.Graph(figure=sc), dash_table.DataTable(data=c.to_dict("records"), columns=[{"name": i, "id": i} for i in c.columns])]

if __name__ == "__main__":
    app.run(debug=True, port=8050)
