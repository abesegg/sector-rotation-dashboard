import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pc
import requests
import re
import io
from datetime import datetime, timedelta

st.set_page_config(
    page_title="日本株式 セクターローテーション",
    page_icon="📊",
    layout="wide",
)

# NEXT FUNDS TOPIX-17 ETF (東証上場)  ※ファンド名はyfinance longNameで確認済み
SECTOR_TICKERS: dict[str, str] = {
    "食品":             "1617.T",  # FOODS
    "エネルギー資源":   "1618.T",  # ENERGY RESOURCES
    "建設・資材":       "1619.T",  # CONSTRUCTION & MATERIALS
    "素材・化学":       "1620.T",  # RAW MATERIALS & CHEMICALS
    "医薬品":           "1621.T",  # PHARMACEUTICAL
    "自動車・輸送機":   "1622.T",  # AUTOMOBILES & TRANSPORTATION EQUIPMENT
    "鉄鋼・非鉄":       "1623.T",  # STEEL & NONFERROUS
    "機械":             "1624.T",  # MACHINERY
    "電機・精密":       "1625.T",  # ELECTRIC & PRECISION INSTRUMENTS
    "情報通信・サービス":"1626.T", # IT & SERVICES
    "電力・ガス":       "1627.T",  # ELECTRIC POWER & GAS
    "運輸・物流":       "1628.T",  # TRANSPORTATION & LOGISTICS
    "商社・卸売":       "1629.T",  # COMMERCIAL & WHOLESALE TRADE
    "小売":             "1630.T",  # RETAIL TRADE
    "銀行":             "1631.T",  # BANKS
    "金融（除く銀行）": "1632.T",  # FINANCIALS (EX BANKS)
    "不動産":           "1633.T",  # REAL ESTATE
}
BENCHMARK_TICKER = "1306.T"
BENCHMARK_NAME = "TOPIX"


def _remove_price_spikes(prices: pd.DataFrame, threshold: float = 0.5) -> pd.DataFrame:
    """日次騰落率が ±threshold を超える点を前日値で線形補間（データエラー除去）"""
    cleaned = prices.copy()
    for col in cleaned.columns:
        s = cleaned[col]
        ret = s.pct_change().abs()
        bad = ret > threshold
        # 連続する異常値（例: 0.6円が2日続く）も対象にするため前後をセットで除外
        bad = bad | bad.shift(-1).fillna(False)
        cleaned.loc[bad, col] = float("nan")
        cleaned[col] = cleaned[col].interpolate(method="time")
    return cleaned


@st.cache_data(ttl=3600)
def fetch_prices(tickers: list, start: str, end: str) -> pd.DataFrame:
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"]
    else:
        prices = raw.rename(columns={"Close": tickers[0]}) if "Close" in raw.columns else raw
    prices = prices.dropna(how="all")
    return _remove_price_spikes(prices)


def calc_period_returns(prices: pd.DataFrame) -> pd.DataFrame:
    periods = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}
    rows = {}
    for label, n in periods.items():
        n = min(n, len(prices) - 1)
        rows[label] = (prices.iloc[-1] / prices.iloc[-n - 1] - 1) * 100
    return pd.DataFrame(rows)


def normalize_to_100(prices: pd.DataFrame) -> pd.DataFrame:
    first = prices.bfill().iloc[0]
    return prices / first * 100


# ─── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 設定")
    period_map = {"1ヶ月": 30, "3ヶ月": 90, "6ヶ月": 180, "1年": 365, "2年": 730, "5年": 1825}
    period_label = st.selectbox("表示期間", list(period_map.keys()), index=1)
    days = period_map[period_label]
    end_dt = datetime.today()
    start_dt = end_dt - timedelta(days=days)

    st.divider()
    st.caption("データソース: NEXT FUNDS TOPIX-17 ETF")
    st.caption("ティッカーが取得できないセクターは自動除外します")

# ─── データ取得 ────────────────────────────────────────────────────────────
# ヒートマップは 1W〜3Y の固定ルックバックなので常に3年分取得する
# ラインチャートはサイドバーの表示期間でスライスして使う
HEATMAP_DAYS = 3 * 365 + 30
all_tickers = list(SECTOR_TICKERS.values()) + [BENCHMARK_TICKER]
heatmap_start = end_dt - timedelta(days=HEATMAP_DAYS)

with st.spinner("データ取得中…"):
    prices_full = fetch_prices(
        all_tickers,
        heatmap_start.strftime("%Y-%m-%d"),
        end_dt.strftime("%Y-%m-%d"),
    )

ticker_to_name = {v: k for k, v in SECTOR_TICKERS.items()}
ticker_to_name[BENCHMARK_TICKER] = BENCHMARK_NAME

valid_sector_tickers = [
    t for t in SECTOR_TICKERS.values()
    if t in prices_full.columns and prices_full[t].notna().sum() > 5
]
# ヒートマップ用（3年分）
sector_prices_full = prices_full[valid_sector_tickers].rename(columns=ticker_to_name)
# ラインチャート用（表示期間でスライス）
prices_raw = prices_full.loc[start_dt.strftime("%Y-%m-%d"):]
sector_prices = prices_raw[valid_sector_tickers].rename(columns=ticker_to_name)
has_benchmark = BENCHMARK_TICKER in prices_full.columns

# セクター → 色の固定マッピング（Tab2 / Tab3 共通）
_palette = pc.qualitative.Dark24
SECTOR_COLOR: dict[str, str] = {
    name: _palette[i % len(_palette)]
    for i, name in enumerate(sector_prices.columns)
}

# ─── ヘッダー ──────────────────────────────────────────────────────────────
st.title("📊 日本株式 セクターローテーション")
st.caption(
    f"期間: {start_dt.strftime('%Y/%m/%d')} 〜 {end_dt.strftime('%Y/%m/%d')}"
    f"　|　取得セクター数: {len(sector_prices.columns)}/{len(SECTOR_TICKERS)}"
)

if sector_prices.empty:
    st.error("データを取得できませんでした。ネットワーク環境を確認してください。")
    st.stop()

# ─── KPIカード（1W騰落率 上位5 / 下位5）──────────────────────────────────
returns_all = calc_period_returns(sector_prices_full)  # ヒートマップ用（常に3年分）
returns_1w = returns_all["1W"].sort_values(ascending=False)
top5 = returns_1w.head(5)
bot5 = returns_1w.tail(5)

def _kpi_card(col, label: str, val: float) -> None:
    color = "#2ecc71" if val >= 0 else "#e74c3c"
    arrow = "▲" if val >= 0 else "▼"
    col.markdown(
        f"<div style='font-size:0.70rem;color:#888;height:2.4em;line-height:1.2em;overflow:hidden'>{label}</div>"
        f"<div style='font-size:1.10rem;font-weight:700;color:{color}'>{arrow} {val:+.1f}%</div>",
        unsafe_allow_html=True,
    )

st.caption("直近1週間の騰落率　上位5 / 下位5")
kpi_cols = st.columns(10)
for i, (name, val) in enumerate(top5.items()):
    _kpi_card(kpi_cols[i], f"↑ {name}", val)
for i, (name, val) in enumerate(bot5.items()):
    _kpi_card(kpi_cols[5 + i], f"↓ {name}", val)

st.divider()

# ─── タブ ──────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "🌡️ 騰落率ヒートマップ",
    "📈 パフォーマンス推移",
    "📉 対TOPIX推移",
    "💹 売買代金上位",
])

# ── Tab1: ヒートマップ ────────────────────────────────────────────────────
with tab1:
    st.subheader("セクター別 騰落率ヒートマップ（%）")
    df = returns_all.sort_values("1W", ascending=True)

    sl_col1, sl_col2, _ = st.columns([1, 1, 4])
    cap_pos = sl_col1.slider("上限（緑）%", min_value=5, max_value=200, value=60, step=5)
    cap_neg = sl_col2.slider("下限（赤）%", min_value=5, max_value=200, value=15, step=5)

    # 0 が常に黄色になるカスタムカラースケール
    z = cap_neg / (cap_neg + cap_pos)  # [0,1] 上での 0% の位置
    colorscale = [
        [0,                       "#a50026"],  # 濃い赤
        [z * 0.5,                 "#f46d43"],  # 中赤
        [z * 0.9,                 "#fee08b"],  # 薄黄
        [z,                       "#ffffbf"],  # 黄（= 0%）
        [z + (1 - z) * 0.1,      "#d9ef8b"],  # 薄緑
        [z + (1 - z) * 0.5,      "#66bd63"],  # 中緑
        [1,                       "#006837"],  # 濃い緑
    ]

    fig = go.Figure(go.Heatmap(
        z=df.values,
        x=df.columns.tolist(),
        y=df.index.tolist(),
        colorscale=colorscale,
        zmin=-cap_neg,
        zmax=cap_pos,
        text=[[f"{v:+.1f}%" for v in row] for row in df.values],
        texttemplate="%{text}",
        textfont={"size": 11},
        colorbar=dict(title="%", thickness=15, ticksuffix="%"),
    ))
    fig.update_layout(
        height=max(400, len(df) * 32),
        margin=dict(l=160, r=20, t=30, b=40),
        xaxis=dict(side="top"),
    )
    st.plotly_chart(fig, use_container_width=True)

# ── Tab2: 正規化ラインチャート ────────────────────────────────────────────
with tab2:
    st.subheader("セクター別 パフォーマンス推移（期間始点 = 100）")
    norm = normalize_to_100(sector_prices)
    sorted_cols = norm.iloc[-1].sort_values(ascending=False).index
    fig = go.Figure()
    for col in sorted_cols:
        last_val = norm[col].iloc[-1]
        fig.add_trace(go.Scatter(
            x=norm.index, y=norm[col], name=f"{col}  {last_val:.1f}",
            mode="lines", line=dict(width=1.5, color=SECTOR_COLOR[col]),
        ))

    if has_benchmark:
        bm_norm = normalize_to_100(
            prices_raw[[BENCHMARK_TICKER]].rename(columns={BENCHMARK_TICKER: BENCHMARK_NAME})
        )
        fig.add_trace(go.Scatter(
            x=bm_norm.index, y=bm_norm.iloc[:, 0], name=BENCHMARK_NAME,
            mode="lines", line=dict(width=2.5, dash="2px,2px", color="black"),
        ))

    fig.update_layout(
        height=560,
        hovermode="x unified",
        yaxis_title="指数（始点=100）",
        legend=dict(x=1.01, y=1),
        margin=dict(r=180),
    )
    st.plotly_chart(fig, use_container_width=True)

# ── Tab3: 対TOPIX 超過リターン ────────────────────────────────────────────
with tab3:
    st.subheader("セクター別 対TOPIX推移（%）")
    if not has_benchmark:
        st.warning("TOPIXデータを取得できませんでした")
    else:
        bm_series = prices_raw[BENCHMARK_TICKER]
        excess_df = pd.DataFrame(index=prices_raw.index)
        for t in valid_sector_tickers:
            name = ticker_to_name[t]
            aligned = pd.concat([prices_raw[t], bm_series], axis=1).dropna()
            aligned.columns = ["sector", "bm"]
            sector_ret = aligned["sector"] / aligned["sector"].iloc[0] - 1
            bm_ret = aligned["bm"] / aligned["bm"].iloc[0] - 1
            excess_df[name] = (sector_ret - bm_ret) * 100

        excess_df = excess_df.dropna(how="all")

        # 直近値でソートして凡例を整理
        last_vals = excess_df.iloc[-1].sort_values(ascending=False)

        fig = go.Figure()
        for name in last_vals.index:
            val = last_vals[name]
            fig.add_trace(go.Scatter(
                x=excess_df.index,
                y=excess_df[name],
                name=f"{name}  {val:+.1f}%",
                mode="lines",
                line=dict(width=1.5, color=SECTOR_COLOR.get(name)),
            ))

        fig.add_hline(y=0, line_dash="dot", line_color="black", line_width=1.5)
        fig.update_layout(
            height=560,
            hovermode="x unified",
            yaxis_title="累積超過リターン（%）",
            legend=dict(x=1.01, y=1),
            margin=dict(r=200),
        )
        st.plotly_chart(fig, use_container_width=True)

# ── Tab4: 売買代金上位銘柄 ───────────────────────────────────────────────
_KABUTAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
_MATSUI_URL = "https://finance.matsui.co.jp/ranking-trading-top/index"

def _parse_name_col(s: str):
    m = re.search(r'([A-Z0-9]{4,5})\s+(東[PEGSO]|名[PEGSO])', str(s))
    if m:
        code, market = m.group(1), m.group(2)
        name = s[:s.find(code)].strip()
        return name, code, market
    return s, "", ""

@st.cache_data(ttl=1800)
def fetch_trading_ranking(top_n: int = 20) -> tuple[pd.DataFrame, str]:
    """松井証券 売買代金ランキングを取得。(DataFrame, 参照先更新時刻) を返す"""
    r = requests.get(_MATSUI_URL, headers=_KABUTAN_HEADERS, timeout=15)
    r.raise_for_status()

    # ページ上の更新時刻を抽出
    m = re.search(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}', r.text)
    source_time = m.group(0) if m else "不明"

    raw = pd.read_html(io.StringIO(r.text))[0]
    names, codes, markets = zip(*raw["銘柄名(コード/市場)"].map(_parse_name_col))
    df = pd.DataFrame({
        "ランク":            raw["順位"],
        "コード":            codes,
        "銘柄名":            names,
        "市場":              markets,
        "株価(円)":          raw["現在値"],
        "前日比(%)":         raw["前日比"].str.extract(r'\(([+-−]?[\d,.]+%)\)')[0].str.replace('−', '-', regex=False),
        "売買代金(百万円)":   raw["売買代金"].str.extract(r'([\d,]+)')[0].str.replace(',', '', regex=False).astype(float),
    })
    return df.head(top_n), source_time

def _style_pct(val: str) -> str:
    try:
        v = float(str(val).replace("%", "").replace("+", ""))
        color = "#2ecc71" if v > 0 else "#e74c3c" if v < 0 else "inherit"
        return f"color: {color}; font-weight: 600"
    except Exception:
        return ""

with tab4:
    st.subheader("売買代金上位銘柄")
    st.caption("データ: 松井証券（finance.matsui.co.jp）| 30分キャッシュ | 全市場上位50件から表示")

    try:
        df_rank, source_time = fetch_trading_ranking(top_n=20)

        styled = (
            df_rank.style
            .map(_style_pct, subset=["前日比(%)"])
            .format({"株価(円)": "{:,.1f}", "売買代金(百万円)": "{:,.0f}"})
            .set_properties(**{"text-align": "right"}, subset=["株価(円)", "前日比(%)", "売買代金(百万円)"])
            .set_properties(**{"text-align": "left"}, subset=["銘柄名", "市場"])
            .hide(axis="index")
        )
        st.dataframe(styled, use_container_width=True, height=600)
        st.caption(f"参照先データ更新時刻: {source_time}　|　表示件数: {len(df_rank)} 件")

    except Exception as e:
        st.error(f"データ取得に失敗しました: {e}")
        st.caption("松井証券サイトへのアクセスに失敗した場合は時間をおいて再試行してください。")

st.divider()
st.caption(
    "⚠️ 本ダッシュボードは情報提供目的のみです。"
    "データはyfinance・Kabutanを通じて取得しており実際の数値と差異が生じる場合があります。"
    "投資判断はご自身の責任で行ってください。"
)
