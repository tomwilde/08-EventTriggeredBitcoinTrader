from __future__ import annotations

import os
from statistics import pstdev

import plotly.graph_objects as go
import streamlit as st

from event_agent.engine import AutomationEngine

st.set_page_config(page_title="Event-Triggered Automation Agent", layout="wide")


@st.cache_resource
def get_engine() -> AutomationEngine:
    return AutomationEngine()


engine = get_engine()
is_test_mode = bool(os.getenv("PYTEST_CURRENT_TEST"))

if is_test_mode and not engine.ui_ready:
    engine.state.startup_complete = True
    engine.state.market_outlook = "bullish"

if not engine.ui_ready:
    with st.spinner("Preparing startup context (prices, halving, outlook)..."):
        engine.initialize_startup_context()

if not is_test_mode and not engine.is_running:
    engine.start_background()

st.title("Event-Triggered Bitcoin Automation Agent")

EMA_LABELS = {
    "12_hours": "EMA 12H",
    "6_hours": "EMA 6H",
    "1_hour": "EMA 1H",
    "30_mins": "EMA 30m",
    "10_mins": "EMA 10m",
    "5_mins": "EMA 5m",
}

EMA_COLOR_KEYS_SHORTEST_TO_LONGEST = [
    "5_mins",
    "10_mins",
    "30_mins",
    "1_hour",
    "6_hours",
    "12_hours",
]

EMA_COLORS_SHORTEST_TO_LONGEST = [
    "#3b82f6",
    "#3579e6",
    "#306fd6",
    "#2a66c6",
    "#255cb6",
    "#1f53a6",
]

EMA_LINE_COLORS = {
    key: EMA_COLORS_SHORTEST_TO_LONGEST[idx]
    for idx, key in enumerate(EMA_COLOR_KEYS_SHORTEST_TO_LONGEST)
}

CHART_REFRESH_SECONDS = 15
PANEL_REFRESH_SECONDS = 5


def _chart_signature() -> tuple[int, str | None, float | None, int]:
    prices = engine.state.prices
    latest_timestamp = prices[-1].timestamp.isoformat() if prices else None
    latest_price = prices[-1].price if prices else None
    return (len(prices), latest_timestamp, latest_price, len(engine.state.settled_trades))


def _format_countdown(seconds: int) -> str:
    if seconds <= 0:
        return "now"
    mins, secs = divmod(seconds, 60)
    return f"{mins:02d}:{secs:02d}"


def _rolling_volatility_percent(prices: list[float], window: int = 30) -> list[float | None]:
    if window < 2:
        raise ValueError("window must be >= 2")

    vol: list[float | None] = []
    for idx in range(len(prices)):
        if idx + 1 < window:
            vol.append(None)
            continue

        sample = prices[idx - window + 1 : idx + 1]
        returns: list[float] = []
        for pos in range(1, len(sample)):
            prev = sample[pos - 1]
            if prev == 0:
                continue
            returns.append((sample[pos] - prev) / prev)

        if not returns:
            vol.append(0.0)
            continue

        vol.append(pstdev(returns) * 100)

    return vol


def _build_figure() -> go.Figure:
    series = engine.chart_points()
    figure = go.Figure()
    x_values = [item.timestamp for item in engine.state.prices]
    volatility = _rolling_volatility_percent(series["price"])

    for key, values in series.items():
        if not key.startswith("ma_"):
            continue
        ema_key = key.removeprefix("ma_")
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=values,
                mode="lines",
                name=EMA_LABELS.get(ema_key, key.upper()),
                line={"color": EMA_LINE_COLORS.get(ema_key, "#1f53a6"), "width": 1.8},
            )
        )

    if series["prediction_markers"]:
        colors = []
        texts = []
        for idx, marker in enumerate(series["prediction_markers"]):
            if marker is None:
                colors.append("rgba(0,0,0,0)")
                texts.append("")
                continue

            settled = next(
                (
                    trade
                    for trade in engine.state.settled_trades
                    if engine.state.prices[idx].timestamp == trade.exit.timestamp
                ),
                None,
            )
            if settled is None:
                colors.append("rgba(0,0,0,0)")
                texts.append("")
                continue
            if settled.correct_direction is True:
                colors.append("red")
            elif settled.correct_direction is False:
                colors.append("green")
            else:
                colors.append("#9ca3af")
            texts.append(
                f"{settled.prediction.direction}, mag={settled.prediction.magnitude}, pnl={settled.pnl:.2f}"
            )

        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=series["prediction_markers"],
                mode="markers",
                name="Prediction Outcome",
                marker={"color": colors, "size": 10},
                hovertext=texts,
                hoverinfo="text",
            )
        )

    if any(value is not None for value in volatility):
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=volatility,
                mode="lines",
                name="Volatility % (30)",
                line={"color": "#f59e0b", "width": 1.6, "dash": "dot"},
                yaxis="y2",
            )
        )

    if series["price"]:
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=series["price"],
                mode="lines",
                name="BTC Price",
                line={"color": "#ef4444", "width": 2.5},
            )
        )

    figure.update_layout(
        height=900,
        title="Price / Moving Averages / Predictions",
        xaxis_title="Time (UTC)",
        yaxis_title="Price (USD)",
        yaxis2={
            "title": "Volatility (%)",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
        uirevision="price-chart",
    )
    return figure


def _render_price_chart_panel() -> None:
    st.plotly_chart(_build_figure(), width="stretch", key="price_chart")
    if engine.state.prices:
        latest = engine.state.prices[-1].timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        st.caption(f"Last updated: {latest}")


def _render_runtime_panel() -> None:
    heartbeat_frames = ["●○○", "○●○", "○○●"]
    frame_idx = st.session_state.get("heartbeat_frame_idx", 0)
    st.session_state["heartbeat_frame_idx"] = (frame_idx + 1) % len(heartbeat_frames)

    st.markdown("### Runtime")
    runtime_rows = [
        {"item": "Live heartbeat", "value": heartbeat_frames[frame_idx]},
        {"item": "Price loop", "value": "continuous (auto-rate)"},
        {"item": "Rate-limit backoff", "value": f"{engine.current_poll_interval_seconds:.1f}s"},
        {"item": "Jev decision interval", "value": f"{engine.jev_interval_seconds:.0f}s"},
        {
            "item": "Next price update",
            "value": _format_countdown(engine.seconds_until_next_price_update()),
        },
        {
            "item": "Next Jev decision",
            "value": _format_countdown(engine.seconds_until_next_decision()),
        },
        {
            "item": "Outlook",
            "value": engine.current_outlook.title() if engine.current_outlook is not None else "-",
        },
        {
            "item": "Next halving estimate",
            "value": (
                engine.state.next_halving_at.strftime("%Y-%m-%d %H:%M UTC")
                if engine.state.next_halving_at is not None
                else "-"
            ),
        },
    ]
    st.dataframe(runtime_rows, width="stretch", hide_index=True)

    st.markdown("### Paper Book")
    summary = engine.paper_book_summary
    st.metric("Starting Cash", f"${summary['starting_cash']:,.2f}")
    st.metric("Cash Balance", f"${summary['cash_balance']:,.2f}")
    st.metric("Realized P/L", f"${summary['realized_pnl']:,.2f}")


def _render_activity_panel() -> None:

    st.markdown("### Transactions made")
    if engine.state.settled_trades:
        rows = []
        recent_trades = sorted(
            engine.state.settled_trades[-100:],
            key=lambda trade: trade.exit.timestamp,
            reverse=True,
        )
        for trade in recent_trades:
            units = trade.notional / trade.entry.price if trade.entry.price else 0.0
            rows.append(
                {
                    "closed_at": trade.exit.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "action": "BUY" if trade.prediction.direction == "up" else "SELL",
                    "entry_price": f"${trade.entry.price:,.2f}",
                    "exit_price": f"${trade.exit.price:,.2f}",
                    "notional": f"${trade.notional:,.2f}",
                    "amount_btc": f"{units:.6f}",
                    "p_l": f"${trade.pnl:,.2f}",
                    "result": (
                        "correct"
                        if trade.correct_direction is True
                        else "incorrect" if trade.correct_direction is False else "flat"
                    ),
                    "rationale": trade.prediction.rationale,
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.caption("No settled transactions yet.")

    if engine.state.dead_letters:
        st.markdown("### Dead Letter")
        page_size = 25
        dead_letters_latest_first = list(reversed(engine.state.dead_letters))
        total_items = len(dead_letters_latest_first)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if "dead_letter_page" not in st.session_state:
            st.session_state["dead_letter_page"] = 1

        current_page = min(max(1, st.session_state["dead_letter_page"]), total_pages)
        st.session_state["dead_letter_page"] = current_page

        nav_prev_col, nav_info_col, nav_next_col = st.columns([1, 2, 1])
        with nav_prev_col:
            prev_clicked = st.button("Prev", disabled=current_page <= 1, key="dead_prev")
            if prev_clicked:
                st.session_state["dead_letter_page"] = current_page - 1
                st.rerun()
        with nav_info_col:
            st.caption(f"Page {current_page} of {total_pages} ({total_items} items)")
        with nav_next_col:
            next_clicked = st.button("Next", disabled=current_page >= total_pages, key="dead_next")
            if next_clicked:
                st.session_state["dead_letter_page"] = current_page + 1
                st.rerun()

        start = (current_page - 1) * page_size
        end = start + page_size
        for dead in dead_letters_latest_first[start:end]:
            st.code(dead)


def _render_dashboard_body() -> None:
    graph_col, pnl_col = st.columns([8, 2])
    with graph_col:
        _render_price_chart_panel()
    with pnl_col:
        _render_runtime_panel()
    _render_activity_panel()


@st.fragment(run_every=f"{CHART_REFRESH_SECONDS}s")
def render_live_chart_polling(chart_slot, chart_meta_slot) -> None:
    signature = _chart_signature()
    if st.session_state.get("chart_render_signature") != signature:
        chart_slot.plotly_chart(_build_figure(), width="stretch", key="price_chart")
        st.session_state["chart_render_signature"] = signature

    if engine.state.prices:
        latest = engine.state.prices[-1].timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        chart_meta_slot.caption(f"Last updated: {latest}")
    else:
        chart_meta_slot.caption("Last updated: -")


@st.fragment(run_every=f"{PANEL_REFRESH_SECONDS}s")
def render_live_runtime_panel_polling() -> None:
    _render_runtime_panel()


@st.fragment(run_every=f"{PANEL_REFRESH_SECONDS}s")
def render_live_activity_polling() -> None:
    _render_activity_panel()


if engine.is_running:
    graph_col, pnl_col = st.columns([8, 2])
    with graph_col:
        chart_slot = st.empty()
        chart_meta_slot = st.empty()
        render_live_chart_polling(chart_slot, chart_meta_slot)
    with pnl_col:
        render_live_runtime_panel_polling()
    render_live_activity_polling()
else:
    _render_dashboard_body()
