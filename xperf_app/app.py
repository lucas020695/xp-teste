"""Dashboard Dash — XPerformance Analyzer.

Abas:
  1. Upload       — carrega PDF e extrai portfólio
  2. Portfólio    — alocação, tabela completa de ativos
  3. KPIs         — curva de patrimônio, métricas do portfólio
  4. Snapshot     — busca dados externos e compara com o PDF
"""
from __future__ import annotations

import base64
import tempfile
from datetime import date

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, dash_table

from data_fetcher import (
    classify_asset_source,
    get_cdi_daily,
    get_cdi_returns,
    get_listed_prices,
    synthetic_return_from_strategy,
)
from kpi_engine import compute_asset_kpis, compute_portfolio_kpis
from pdf_parser import extract_pdf

# ---------------------------------------------------------------------------
# Tema / estilos
# ---------------------------------------------------------------------------
C = {
    "bg": "#f7f6f2", "surface": "#f9f8f5", "surface2": "#fbfbf9",
    "border": "#d4d1ca", "text": "#28251d", "muted": "#7a7974",
    "primary": "#01696f", "primary_hover": "#0c4e54",
    "success": "#437a22", "warning": "#964219", "error": "#a12c7b",
    "divider": "#dcd9d5",
}

def card(children, **kwargs):
    style = {
        "background": C["surface"], "border": f"1px solid {C['border']}",
        "borderRadius": "10px", "padding": "20px",
        "boxShadow": "0 1px 4px rgba(40,37,29,0.07)", "marginBottom": "16px",
    }
    style.update(kwargs.get("style", {}))
    return html.Div(children, style=style)


def kpi_card(label: str, value: str, color: str = C["primary"]):
    return html.Div([
        html.Div(value, style={"fontSize": "1.8rem", "fontWeight": "700", "color": color}),
        html.Div(label, style={"fontSize": "0.82rem", "color": C["muted"], "marginTop": "4px"}),
    ], style={
        "background": C["surface"], "border": f"1px solid {C['border']}",
        "borderRadius": "10px", "padding": "18px 22px",
        "boxShadow": "0 1px 4px rgba(40,37,29,0.07)",
        "minWidth": "160px", "textAlign": "center",
    })


DT_STYLE = {
    "style_table": {"overflowX": "auto", "borderRadius": "8px", "border": f"1px solid {C['border']}"},
    "style_header": {"backgroundColor": C["surface2"], "fontWeight": "700", "color": C["text"], "fontSize": "0.82rem"},
    "style_cell": {"padding": "8px 12px", "fontSize": "0.83rem", "color": C["text"],
                   "backgroundColor": C["surface"], "border": f"1px solid {C['divider']}"},
    "style_data_conditional": [
        {"if": {"row_index": "odd"}, "backgroundColor": C["bg"]},
    ],
    "page_size": 20,
    "filter_action": "native",
    "sort_action": "native",
}

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
app = Dash(__name__, title="XPerf Analyzer")
server = app.server  # para gunicorn

app.layout = html.Div(style={"background": C["bg"], "minHeight": "100vh", "padding": "16px", "fontFamily": "'Segoe UI', system-ui, sans-serif"}, children=[
    # Header
    html.Div([
        html.H1("XPerformance Analyzer", style={"color": C["primary"], "margin": "0", "fontSize": "1.4rem", "fontWeight": "700"}),
        html.Span("Análise independente via BCB · Yahoo Finance · CVM", style={"color": C["muted"], "fontSize": "0.82rem"}),
    ], style={"marginBottom": "20px", "paddingBottom": "12px", "borderBottom": f"1px solid {C['divider']}"}),

    # Stores
    dcc.Store(id="store-data"),
    dcc.Store(id="store-comp"),
    dcc.Store(id="store-kpis"),

    # Tabs
    dcc.Tabs(id="tabs", value="upload", style={"marginBottom": "16px"}, children=[

        # ── ABA 1: Upload ──────────────────────────────────────────────────
        dcc.Tab(label="📤 Upload", value="upload", children=[
            card([
                html.H3("Carregar PDF XPerformance", style={"marginTop": 0, "color": C["text"]}),
                html.P("Arraste ou clique para selecionar o relatório PDF da XP.",
                       style={"color": C["muted"], "marginBottom": "12px"}),
                dcc.Upload(
                    id="upload-pdf",
                    children=html.Div([
                        html.Span("📁 "),
                        html.Strong("Clique ou arraste o PDF aqui"),
                    ]),
                    style={
                        "border": f"2px dashed {C['primary']}", "borderRadius": "10px",
                        "padding": "32px", "textAlign": "center", "cursor": "pointer",
                        "background": C["bg"], "color": C["primary"], "marginBottom": "12px",
                    },
                    max_size=50 * 1024 * 1024,
                ),
                dcc.Loading(id="loading-upload", children=html.Div(id="upload-status"), type="circle"),
            ])
        ]),

        # ── ABA 2: Portfólio ───────────────────────────────────────────────
        dcc.Tab(label="📋 Portfólio", value="portfolio", children=[
            dcc.Loading(html.Div(id="portfolio-view"))
        ]),

        # ── ABA 3: KPIs ────────────────────────────────────────────────────
        dcc.Tab(label="📊 KPIs", value="kpis", children=[
            dcc.Loading(html.Div(id="kpis-view"))
        ]),

        # ── ABA 4: Snapshot / Discrepâncias ───────────────────────────────
        dcc.Tab(label="📸 Snapshot", value="snap", children=[
            card([
                html.P("Clique para buscar preços independentes via Yahoo Finance e BCB e comparar com o PDF.",
                       style={"color": C["muted"], "margin": "0 0 12px"}),
                html.Button(
                    "🔄 Buscar Dados Externos", id="btn-fetch",
                    style={
                        "background": C["primary"], "color": "white", "border": "none",
                        "borderRadius": "8px", "padding": "10px 20px", "cursor": "pointer",
                        "fontWeight": "600", "fontSize": "0.9rem",
                    }
                ),
            ]),
            dcc.Loading(html.Div(id="snapshot-view"), type="circle"),
        ]),
    ])
])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@app.callback(
    Output("store-data", "data"),
    Output("upload-status", "children"),
    Input("upload-pdf", "contents"),
    State("upload-pdf", "filename"),
    prevent_initial_call=True,
)
def cb_upload(contents, filename):
    if not contents:
        return dash.no_update, "Nenhum arquivo."
    raw = base64.b64decode(contents.split(",", 1)[1])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(raw)
        tmp_path = f.name

    parsed = extract_pdf(tmp_path)
    assets = parsed["assets"]

    warnings_html = []
    for w in parsed["parse_warnings"]:
        warnings_html.append(html.P(f"⚠️ {w}", style={"color": C["warning"]}))

    status = html.Div([
        html.P(f"✅ {filename} carregado com sucesso!",
               style={"color": C["success"], "fontWeight": "600"}),
        html.P(f"Data referência: {parsed['ref_date']} | Ativos: {len(assets)} | "
               f"Saldo total: R$ {assets['saldo_bruto'].sum():,.2f} | "
               f"Método: {parsed['extraction_method']}",
               style={"color": C["muted"]}),
        *warnings_html,
    ])

    payload = {
        "assets": assets.to_dict("records"),
        "ref_date": str(parsed["ref_date"]),
        "monthly_returns": parsed["portfolio_monthly_returns"],
        "warnings": parsed["parse_warnings"],
    }
    return payload, status


@app.callback(
    Output("portfolio-view", "children"),
    Input("store-data", "data"),
)
def cb_portfolio(data):
    if not data:
        return html.P("Faça upload de um PDF na aba Upload.", style={"color": C["muted"]})

    df = pd.DataFrame(data["assets"])

    # Pie por estratégia
    by_strat = df.groupby("strategy", as_index=False)["saldo_bruto"].sum()
    pie = px.pie(
        by_strat, names="strategy", values="saldo_bruto", hole=0.45,
        title="Alocação por Estratégia",
        color_discrete_sequence=px.colors.qualitative.Safe,
    )
    pie.update_traces(textposition="inside", textinfo="percent+label")
    pie.update_layout(showlegend=True, height=420, paper_bgcolor=C["surface"],
                      plot_bgcolor=C["surface"], font_color=C["text"])

    # Tabela de ativos
    show_cols = ["strategy", "name", "ticker", "qty", "saldo_bruto",
                 "alloc_pct", "rent_mes", "pct_cdi_mes", "rent_ano", "pct_cdi_ano"]
    tbl = dash_table.DataTable(
        data=df[show_cols].to_dict("records"),
        columns=[{"name": c, "id": c} for c in show_cols],
        **DT_STYLE,
    )

    total = df["saldo_bruto"].sum()
    return [
        html.Div([
            kpi_card("Total Bruto", f"R$ {total:,.0f}"),
            kpi_card("Nº de Ativos", str(len(df))),
            kpi_card("Estratégias", str(df["strategy"].nunique())),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "16px"}),
        card([dcc.Graph(figure=pie)]),
        card([html.H3("Posições", style={"marginTop": 0}), tbl]),
    ]


@app.callback(
    Output("kpis-view", "children"),
    Output("store-kpis", "data"),
    Input("store-data", "data"),
)
def cb_kpis(data):
    if not data:
        return html.P("Faça upload de um PDF.", style={"color": C["muted"]}), None

    monthly = data.get("monthly_returns", [])
    ref_date = date.fromisoformat(data["ref_date"]) if data.get("ref_date") else date.today()

    # Busca CDI
    try:
        cdi_daily = get_cdi_daily(ref_date)
        cdi = get_cdi_returns(ref_date, cdi_daily)
    except Exception:
        cdi = {"mes": 0, "ano": 0, "12m": 0, "24m": 0}

    if not monthly:
        return html.P("Retornos mensais não extraídos do PDF.", style={"color": C["warning"]}), None

    kpis = compute_portfolio_kpis(monthly, cdi)

    # Curva acumulada
    arr = pd.Series(monthly, dtype=float)
    equity = (1 + arr / 100).cumprod() * 100

    fig_curve = go.Figure()
    fig_curve.add_trace(go.Scatter(
        y=equity, mode="lines", name="Portfólio",
        line={"color": C["primary"], "width": 2},
        fill="tozeroy", fillcolor=f"rgba(1,105,111,0.08)",
    ))
    fig_curve.update_layout(
        title="Curva de Patrimônio Acumulado (base 100)",
        xaxis_title="Meses", yaxis_title="Índice",
        height=350, paper_bgcolor=C["surface"], plot_bgcolor=C["surface"],
        font_color=C["text"],
    )

    def _color(v: float) -> str:
        return C["success"] if v >= 0 else C["error"]

    kpi_row = html.Div([
        kpi_card("Retorno 12M", f"{kpis['return_12m']:.2f}%", _color(kpis["return_12m"])),
        kpi_card("CDI 12M", f"{kpis['cdi_12m']:.2f}%"),
        kpi_card("vs CDI 12M", f"{kpis['return_vs_cdi_12m_pp']:+.2f} pp", _color(kpis["return_vs_cdi_12m_pp"])),
        kpi_card("% CDI 12M", f"{kpis['return_vs_cdi_12m_pct']:.1f}%"),
        kpi_card("Max DD 24M", f"{kpis['max_drawdown_24m']:.2f}%", C["error"]),
        kpi_card("Sharpe 12M", f"{kpis['sharpe_12m']:.2f}"),
        kpi_card("Vol 12M (a.a.)", f"{kpis['vol_12m_ann']:.2f}%"),
        kpi_card("Hit Rate 12M", f"{kpis['hit_rate_12m']:.0f}%"),
    ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "marginBottom": "16px"})

    return [kpi_row, card([dcc.Graph(figure=fig_curve)])], kpis


@app.callback(
    Output("store-comp", "data"),
    Input("btn-fetch", "n_clicks"),
    State("store-data", "data"),
    prevent_initial_call=True,
)
def cb_fetch_external(_, data):
    if not data:
        return []

    df = pd.DataFrame(data["assets"])
    ref_date = date.fromisoformat(data["ref_date"]) if data.get("ref_date") else date.today()

    # CDI
    try:
        cdi_daily = get_cdi_daily(ref_date)
        cdi = get_cdi_returns(ref_date, cdi_daily)
    except Exception:
        cdi = {"mes": 0.0, "ano": 0.0, "12m": 0.0, "24m": 0.0}

    # Tickers listados
    tickers = sorted(set(t for t in df["ticker"].dropna() if str(t).strip()))
    ext_prices = get_listed_prices(tickers, ref_date) if tickers else {}

    rows = []
    for _, r in df.iterrows():
        t = str(r.get("ticker", "")).strip()
        src = classify_asset_source(r.get("name", ""), t)
        pdf_mes = float(r.get("rent_mes", 0.0) or 0.0)
        pdf_ano = float(r.get("rent_ano", 0.0) or 0.0)

        if t and t in ext_prices:
            ext_mes = ext_prices[t]["mes"]
            ext_ano = ext_prices[t]["ano"]
        else:
            ext_mes = synthetic_return_from_strategy(r, "mes")
            ext_ano = synthetic_return_from_strategy(r, "ano")
            if src == "listed":
                src = "listed-not-found"
            else:
                src = f"{src}-proxy"

        delta = ext_mes - pdf_mes
        flag = (
            "✅ match" if abs(delta) < 0.5 else
            "🟡 alerta" if abs(delta) < 2.0 else
            "🔴 diverge"
        )

        rows.append({
            "ticker": t or r.get("name", "")[:30],
            "name": str(r.get("name", ""))[:50],
            "strategy": r.get("strategy", ""),
            "source": src,
            "pdf_mes": round(pdf_mes, 3),
            "ext_mes": round(ext_mes, 3),
            "delta_mes": round(delta, 3),
            "pdf_ano": round(pdf_ano, 3),
            "ext_ano": round(ext_ano, 3),
            "delta_ano": round(ext_ano - pdf_ano, 3),
            "flag": flag,
            "cdi_mes": round(cdi["mes"], 3),
            "cdi_ano": round(cdi["ano"], 3),
        })

    return rows


@app.callback(
    Output("snapshot-view", "children"),
    Input("store-comp", "data"),
)
def cb_snapshot(comp):
    if not comp:
        return html.P("Clique em 'Buscar Dados Externos' para carregar a comparação.",
                      style={"color": C["muted"]})

    c = pd.DataFrame(comp)
    total = len(c)
    match_ = (c["flag"] == "✅ match").sum()
    alert_ = (c["flag"] == "🟡 alerta").sum()
    div_ = (c["flag"] == "🔴 diverge").sum()

    # KPI cards
    kpi_row = html.Div([
        kpi_card("Total tickers", str(total)),
        kpi_card("✅ Match (<0.5pp)", str(match_), C["success"]),
        kpi_card("🟡 Alerta (0.5-2pp)", str(alert_), C["warning"]),
        kpi_card("🔴 Diverge (>2pp)", str(div_), C["error"]),
    ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "marginBottom": "16px"})

    # Waterfall de deltas
    c_sorted = c.sort_values("delta_mes", key=abs, ascending=False).head(40)
    colors = [C["success"] if d >= 0 else C["error"] for d in c_sorted["delta_mes"]]
    fig_wf = go.Figure(go.Bar(
        x=c_sorted["ticker"], y=c_sorted["delta_mes"],
        marker_color=colors,
        hovertemplate="<b>%{x}</b><br>Δ = %{y:.3f} pp<br>",
    ))
    fig_wf.update_layout(
        title="Discrepância por Ticker: Independente − PDF (pp)",
        xaxis_title="Ticker", yaxis_title="Δ Retorno Mês (pp)",
        height=380, paper_bgcolor=C["surface"], plot_bgcolor=C["surface"],
        font_color=C["text"],
    )
    fig_wf.add_hline(y=0.5, line_dash="dot", line_color=C["warning"], opacity=0.6)
    fig_wf.add_hline(y=-0.5, line_dash="dot", line_color=C["warning"], opacity=0.6)

    # Scatter PDF vs Independente
    fig_sc = px.scatter(
        c, x="pdf_mes", y="ext_mes", color="flag",
        hover_data=["ticker", "name", "delta_mes", "source"],
        color_discrete_map={"✅ match": C["success"], "🟡 alerta": C["warning"], "🔴 diverge": C["error"]},
        title="PDF vs Independente — Retorno Mês (%)",
    )
    # Linha diagonal perfeita
    mn = min(c["pdf_mes"].min(), c["ext_mes"].min()) - 1
    mx = max(c["pdf_mes"].max(), c["ext_mes"].max()) + 1
    fig_sc.add_trace(go.Scatter(x=[mn, mx], y=[mn, mx], mode="lines",
                                line={"dash": "dash", "color": C["muted"]}, name="Linha perfeita"))
    fig_sc.update_layout(height=380, paper_bgcolor=C["surface"], plot_bgcolor=C["surface"],
                         font_color=C["text"])

    # Tabela com cores condicionais
    show_cols = ["flag", "ticker", "strategy", "source",
                 "pdf_mes", "ext_mes", "delta_mes",
                 "pdf_ano", "ext_ano", "delta_ano"]
    tbl = dash_table.DataTable(
        data=c[show_cols].to_dict("records"),
        columns=[{"name": col, "id": col} for col in show_cols],
        style_data_conditional=[
            {"if": {"filter_query": "{flag} = '✅ match'"},
             "backgroundColor": "#d4dfcc", "color": C["text"]},
            {"if": {"filter_query": "{flag} = '🟡 alerta'"},
             "backgroundColor": "#e7d7c4", "color": C["text"]},
            {"if": {"filter_query": "{flag} = '🔴 diverge'"},
             "backgroundColor": "#e0ced7", "color": C["text"]},
            {"if": {"row_index": "odd"}, "backgroundColor": C["bg"]},
        ],
        **{k: v for k, v in DT_STYLE.items() if k != "style_data_conditional"},
    )

    note = html.Div([
        html.Strong("ℹ️ Sobre as discrepâncias: "),
        "Yahoo Finance ajusta preços retroativamente por dividendos (ex-data). ",
        "Para FIIs, isso pode causar diferenças de ±0,3pp em relação ao PDF. ",
        "Fundos abertos (FIF, FIDC) precisam do CNPJ para busca via CVM — ",
        "esses aparecem como 'cvm_fund-proxy' com retorno do PDF como fallback. ",
        "Estruturados e produtos sem série pública são marcados como 'synthetic-proxy'.",
    ], style={"background": C["surface2"], "border": f"1px solid {C['border']}",
              "borderRadius": "8px", "padding": "12px", "fontSize": "0.82rem",
              "color": C["muted"], "marginTop": "12px"})

    return [
        kpi_row,
        card([dcc.Graph(figure=fig_wf)]),
        card([dcc.Graph(figure=fig_sc)]),
        card([html.H3("Tabela Completa de Discrepâncias", style={"marginTop": 0}), tbl, note]),
    ]


if __name__ == "__main__":
    app.run(debug=True, port=8050)
