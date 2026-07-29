import streamlit as st
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

BENCHMARK_NAME = "日経500種"
_NK500_URL = "https://indexes.nikkei.co.jp/nkave/historical/nikkei_500_stock_average_daily_jp.csv"
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


@st.cache_data(ttl=3600)
def fetch_nk500() -> tuple[pd.DataFrame, pd.Series]:
    """日経500種 業種別指数CSVを取得。(sectors_df, benchmark_series) を返す"""
    r = requests.get(_NK500_URL, headers=_REQUEST_HEADERS, timeout=15)
    r.raise_for_status()
    text = r.content.decode("shift_jis", errors="replace")

    df = pd.read_csv(io.StringIO(text), index_col=0, on_bad_lines="skip")
    # 日付に変換できない行（著作権表示など）を除外
    df.index = pd.to_datetime(df.index, format="%Y/%m/%d", errors="coerce")
    df = df[df.index.notna()].sort_index()
    df = df.apply(pd.to_numeric, errors="coerce")

    benchmark = df["終値"].rename(BENCHMARK_NAME)

    sector_cols = [c for c in df.columns if "業種別" in c]
    sector_df = df[sector_cols].copy()
    rename_map = {
        c: re.sub(r"業種別（(.+?)）終値", r"\1", c).strip()
        for c in sector_cols
    }
    sector_df = sector_df.rename(columns=rename_map)

    return sector_df, benchmark


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


# ─── データ取得（キャッシュ済みのため高速・サイドバーのmin_value計算に必要）
with st.spinner("データ取得中…"):
    sector_prices_full, benchmark_full = fetch_nk500()

data_start = sector_prices_full.index[0].date()
data_end = sector_prices_full.index[-1].date()

# ─── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 設定")
    period_map = {
        "1ヶ月": 30,
        "3ヶ月": 90,
        "6ヶ月": 180,
        "1年": 365,
        "2年": 730,
        "3年": 1095,
    }
    period_label = st.selectbox("表示期間", list(period_map.keys()), index=0)
    days = period_map[period_label]
    today = datetime.today().date()
    # 基準日の最小値 = データ開始日 + 表示期間（これより前を選ぶと期間内にデータがなくなる）
    min_end_date = data_start + timedelta(days=days)
    # 期間が変わったときに古い日付が残らないよう key を期間名に連動
    end_date = st.date_input(
        "基準日", value=today,
        min_value=min_end_date, max_value=today,
        key=f"end_date_{period_label}",
    )
    end_dt = datetime.combine(end_date, datetime.min.time())
    start_dt = end_dt - timedelta(days=days)

    st.divider()
    st.caption("データソース: 日経500種業種別指数")
    st.caption("（日本経済新聞社）")

# 基準日以前のデータでヒートマップ・KPIを計算（表示期間に引きずられない）
sector_prices_upto = sector_prices_full.loc[:end_dt.strftime("%Y-%m-%d")]
benchmark_upto = benchmark_full.loc[:end_dt.strftime("%Y-%m-%d")]

# ラインチャート用（基準日から表示期間分遡ってスライス）
sector_prices = sector_prices_full.loc[
    start_dt.strftime("%Y-%m-%d"):end_dt.strftime("%Y-%m-%d")
]
benchmark_prices = benchmark_full.loc[
    start_dt.strftime("%Y-%m-%d"):end_dt.strftime("%Y-%m-%d")
]

# セクター → 色の固定マッピング（36色対応: Dark24 + Light24 = 48色）
_palette = pc.qualitative.Dark24 + pc.qualitative.Light24
SECTOR_COLOR: dict[str, str] = {
    name: _palette[i % len(_palette)]
    for i, name in enumerate(sector_prices_full.columns)
}

# ─── ヘッダー ──────────────────────────────────────────────────────────────
st.title("📊 日本株式 セクターローテーション")
display_start = sector_prices.index[0] if not sector_prices.empty else data_start
actual_end = sector_prices_upto.index[-1] if not sector_prices_upto.empty else end_dt
st.caption(
    f"表示期間: {display_start.strftime('%Y/%m/%d')} 〜 {actual_end.strftime('%Y/%m/%d')}"
    f"　|　セクター数: {len(sector_prices_full.columns)}"
)

if sector_prices.empty:
    st.error("データを取得できませんでした。ネットワーク環境を確認してください。")
    st.stop()

# ─── KPIカード（1W騰落率 上位5 / 下位5）──────────────────────────────────
returns_all = calc_period_returns(sector_prices_upto)
returns_1w = returns_all["1W"].sort_values(ascending=False)
top5 = returns_1w.head(5)
bot5 = returns_1w.tail(5)


def _kpi_card(col, label: str, val: float) -> None:
    color = "#2ecc71" if val >= 0 else "#e74c3c"
    arrow = "▲" if val >= 0 else "▼"
    col.markdown(
        f"<div style='font-size:0.70rem;color:#888;height:2.4em;line-height:1.2em;overflow:hidden'>{label}</div>"
        f"<div style='font-size:1.00rem;font-weight:700;color:{color}'>{arrow} {val:+.1f}%</div>",
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
    "📉 対日経500種推移",
    "💹 売買代金上位",
])

# ── Tab1: ヒートマップ ────────────────────────────────────────────────────
with tab1:
    st.subheader("セクター別 騰落率ヒートマップ（%）")
    df = returns_all.sort_values("1W", ascending=True)

    sl_col1, sl_col2, _ = st.columns([1, 1, 4])
    cap_pos = sl_col1.slider("上限（緑）%", min_value=5, max_value=200, value=60, step=5)
    cap_neg = sl_col2.slider("下限（赤）%", min_value=5, max_value=200, value=15, step=5)

    z = cap_neg / (cap_neg + cap_pos)
    colorscale = [
        [0,                       "#a50026"],
        [z * 0.5,                 "#f46d43"],
        [z * 0.9,                 "#fee08b"],
        [z,                       "#ffffbf"],
        [z + (1 - z) * 0.1,      "#d9ef8b"],
        [z + (1 - z) * 0.5,      "#66bd63"],
        [1,                       "#006837"],
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
        height=max(500, len(df) * 30),
        margin=dict(l=120, r=20, t=30, b=40),
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

    bm_norm = normalize_to_100(benchmark_prices.to_frame())
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

# ── Tab3: 対日経500種 超過リターン ────────────────────────────────────────
with tab3:
    st.subheader("セクター別 対日経500種推移（%）")

    bm_series = benchmark_prices
    excess_df = pd.DataFrame(index=sector_prices.index)
    for col in sector_prices.columns:
        aligned = pd.concat([sector_prices[col], bm_series], axis=1).dropna()
        aligned.columns = ["sector", "bm"]
        sector_ret = aligned["sector"] / aligned["sector"].iloc[0] - 1
        bm_ret = aligned["bm"] / aligned["bm"].iloc[0] - 1
        excess_df[col] = (sector_ret - bm_ret) * 100

    excess_df = excess_df.dropna(how="all")
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
_SW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
_SW_URL = "https://finance.stockweather.co.jp/contents/ranking.aspx?type=13&mkt=0&cat=0000"


def _parse_sw_name(s: str):
    """'キオクシアＨＤ （285A）' → (name, code)"""
    m = re.search(r"（([A-Z0-9]{4,5})）", str(s))
    if m:
        code = m.group(1)
        name = s[:s.rfind("（")].strip()
        return name, code
    return s, ""


@st.cache_data(ttl=1800)
def fetch_trading_ranking(top_n: int = 20) -> tuple[pd.DataFrame, str]:
    """StockWeather 売買代金ランキングを取得。(DataFrame, 参照先更新時刻) を返す"""
    r = requests.get(_SW_URL, headers=_SW_HEADERS, timeout=15)
    r.raise_for_status()

    m = re.search(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}", r.text)
    source_time = m.group(0) if m else "不明"

    raw = pd.read_html(io.StringIO(r.text))[0]
    parsed = raw["銘柄名 （コード）"].map(_parse_sw_name)
    names, codes = zip(*parsed)
    df = pd.DataFrame({
        "ランク":           raw["順位"],
        "コード":           codes,
        "銘柄名":           names,
        "市場":             raw["市場"],
        "株価(円)":         raw["現在値"],
        "前日比(%)":        raw["前日比.1"],
        "売買代金(百万円)":  raw["売買代金"],
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
    st.caption("データ: StockWeather（finance.stockweather.co.jp）| 30分キャッシュ | 全市場上位100件から表示")

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
        st.caption("StockWeatherサイトへのアクセスに失敗した場合は時間をおいて再試行してください。")

st.divider()
st.caption(
    "⚠️ 本ダッシュボードは情報提供目的のみです。"
    "データは日本経済新聞社・StockWeatherを通じて取得しており実際の数値と差異が生じる場合があります。"
    "投資判断はご自身の責任で行ってください。"
)
