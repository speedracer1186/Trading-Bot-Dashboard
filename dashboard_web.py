"""
dashboard_web.py — Trading Bot v7.0 Web Dashboard
===================================================
Password-gated Streamlit dashboard for paper + live trading monitoring.

v7.0 changes:
  - Version updated to v7.0
  - New tickers: HOOD, MSFT, SMCI, XLF, XLE (31 total)
  - PDT counter status panel
  - ORB (Opening Range Breakout) signal indicator
  - New exit reasons: trailing_stop displayed alongside stop_loss/take_profit
  - Session CSV now shows exit_price, pnl, pnl_pct, exit_reason columns
  - Swing hold mode indicator
  - Score 60+ high conviction trade highlights

Deployment: Streamlit Cloud (free tier) — share.streamlit.io
Required secrets (App settings → Secrets):
    DASHBOARD_PASSWORD = "your-chosen-password"
    ALPACA_API_KEY     = "your-alpaca-paper-api-key"
    ALPACA_SECRET_KEY  = "your-alpaca-paper-secret-key"
    ALPACA_PAPER       = "true"
    GITHUB_TOKEN       = "ghp_..."
    GITHUB_REPO        = "speedracer1186/Trading-Bot-Dashboard"
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Bot v7.0",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Password gate ─────────────────────────────────────────────────────────────
def _check_password() -> bool:
    def _submit():
        try:
            correct = st.secrets["DASHBOARD_PASSWORD"]
        except Exception:
            correct = "changeme"
        if st.session_state.get("pw") == correct:
            st.session_state["auth"] = True
        else:
            st.session_state["auth"] = False
            st.session_state["pw_wrong"] = True

    if st.session_state.get("auth"):
        return True

    st.markdown("## 📈 Trading Bot Dashboard")
    st.text_input("Password", type="password", key="pw", on_change=_submit)
    if st.session_state.get("pw_wrong"):
        st.error("Incorrect password.")
    return False

if not _check_password():
    st.stop()

# ── Alpaca client ─────────────────────────────────────────────────────────────
@st.cache_resource
def _get_client():
    from alpaca.trading.client import TradingClient
    ak  = st.secrets.get("ALPACA_API_KEY",    "")
    sk  = st.secrets.get("ALPACA_SECRET_KEY", "")
    pap = str(st.secrets.get("ALPACA_PAPER", "true")).lower() == "true"
    if not ak or not sk:
        raise ValueError(
            f"API keys missing from Streamlit secrets. "
            f"ALPACA_API_KEY={'SET' if ak else 'MISSING'}, "
            f"ALPACA_SECRET_KEY={'SET' if sk else 'MISSING'}. "
            f"Go to share.streamlit.io → your app → Settings → Secrets and add them."
        )
    return TradingClient(api_key=ak, secret_key=sk, paper=pap), pap

try:
    client, is_paper = _get_client()
except Exception as e:
    st.error(f"Alpaca connection failed: {e}")
    st.info(
        "**To fix:** Go to share.streamlit.io → your app → ⋮ Settings → Secrets  \n"
        "Make sure these keys exist with no extra quotes:  \n"
        "```\n"
        "ALPACA_API_KEY = \"PKxxxxxxxxx\"\n"
        "ALPACA_SECRET_KEY = \"xxxxxxxxxx\"\n"
        "ALPACA_PAPER = \"true\"\n"
        "```\n"
        "Then click **Reboot app** from the ⋮ menu."
    )
    st.stop()

# ── Helpers ───────────────────────────────────────────────────────────────────
def _market_status() -> str:
    try:
        clock = client.get_clock()
        if clock.is_open:
            return "OPEN"
        next_open = clock.next_open
        now = datetime.now(timezone.utc)
        diff = next_open - now
        hours = int(diff.total_seconds() // 3600)
        mins  = int((diff.total_seconds() % 3600) // 60)
        return f"CLOSED — opens in {hours}h {mins}m"
    except Exception:
        return "MARKET STATUS UNKNOWN"

def _fetch_session_trades() -> pd.DataFrame:
    """Fetch today's session trade CSV from the GitHub repo."""
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo  = st.secrets.get("GITHUB_REPO",  "speedracer1186/Trading-Bot-Dashboard")
        today = datetime.now().strftime("%Y%m%d")
        headers = {"Authorization": f"token {token}"} if token else {}
        url = f"https://api.github.com/repos/{repo}/contents/"
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return pd.DataFrame()
        files = [f["name"] for f in resp.json()
                 if f["name"].startswith(f"session_paper_{today}") and f["name"].endswith(".csv")]
        if not files:
            return pd.DataFrame()
        # Get most recent file
        fname = sorted(files)[-1]
        dl_url = f"https://raw.githubusercontent.com/{repo}/main/{fname}"
        df = pd.read_csv(dl_url)
        return df
    except Exception:
        return pd.DataFrame()

# ── Auto-refresh (preserves login session state) ─────────────────────────────
# Uses streamlit-autorefresh instead of meta http-equiv which wipes session state
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=30_000, key="dashboard_refresh")
except ImportError:
    # Fallback: show manual refresh button — do NOT use meta refresh (logs you out)
    if st.button("🔄 Refresh", key="manual_refresh"):
        st.rerun()

# ── Header ────────────────────────────────────────────────────────────────────
mode    = "🟡 PAPER" if is_paper else "🔴 LIVE"
mstatus = _market_status()
mcolor  = "green" if mstatus == "OPEN" else "orange" if "opens" in mstatus else "red"

st.markdown(f"# 📈 Trading Bot v7.31.2 &nbsp;&nbsp; {mode}")
st.caption(
    f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET  "
    f"· Auto-refreshes every 30s  "
    f"· Market: :{mcolor}[{mstatus}]"
)

# ── Account summary ───────────────────────────────────────────────────────────
daily_pl = 0.0
equity   = 0.0
try:
    acct         = client.get_account()
    equity       = float(acct.equity)
    cash         = float(acct.cash)
    buying_pow   = float(acct.buying_power)
    last_equity  = float(getattr(acct, "last_equity", equity))
    daily_pl     = equity - last_equity
    daily_pl_pct = (daily_pl / last_equity * 100) if last_equity > 0 else 0.0

    # v7.31: dynamic daily goal based on capital tier (inline tier lookup)
    def _dashboard_daily_goal(eq):
        _t = [
            (0,     1500,    40),   (1500,  2500,   70),
            (2500,  5000,   110),   (5000, 10000,  190),
            (10000, 25000,  350),   (25000, 50000, 650),
            (50000, 75000,  940),   (75000, 100000, 1090),
        ]
        for lo, hi, dly in _t:
            if lo <= eq < hi:
                return float(dly)
        if eq >= 100000:
            return max(1000.0, eq * 0.010)  # Elite scales with equity
        return 40.0  # fallback to Seed
    DAILY_GOAL = _dashboard_daily_goal(equity)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity",       f"${equity:,.2f}")
    c2.metric("Cash",         f"${cash:,.2f}")
    c3.metric("Buying Power", f"${buying_pow:,.2f}")
    c4.metric("Daily P&L",    f"${daily_pl:+,.2f}", f"{daily_pl_pct:+.2f}%",
              delta_color="normal")
    c5.metric("Goal Progress",
              f"{min(daily_pl / DAILY_GOAL * 100, 100):.0f}%" if DAILY_GOAL > 0 else "—",
              f"target ${DAILY_GOAL:,.0f}/day", delta_color="off")

    bar_pct = min(max(daily_pl / DAILY_GOAL, 0), 1.0) if DAILY_GOAL > 0 else 0
    st.progress(bar_pct, text=f"Daily goal: ${daily_pl:+,.2f} / ${DAILY_GOAL:,.0f}")

except Exception as e:
    st.error(f"Account error: {e}")

st.divider()

# ── Open positions + risk/status panel ───────────────────────────────────────
left, right = st.columns([3, 1])

with left:
    st.subheader("Open Positions")
    try:
        positions = client.get_all_positions()
        if not positions:
            st.info("No open positions.")
        else:
            rows = []
            total_cost = 0.0
            for p in positions:
                qty     = int(float(p.qty))
                entry   = float(p.avg_entry_price)
                current = float(p.current_price)
                cost    = abs(qty) * entry
                unreal  = float(p.unrealized_pl)
                unr_pct = float(p.unrealized_plpc) * 100
                total_cost += cost
                rows.append({
                    "Symbol":   p.symbol,
                    "Qty":      qty,
                    "Entry":    f"${entry:.2f}",
                    "Current":  f"${current:.2f}",
                    "Cost":     f"${cost:,.0f}",
                    "Deployed": f"{cost/equity*100:.1f}%" if equity > 0 else "-",
                    "P&L $":    f"${unreal:+.2f}",
                    "P&L %":    f"{unr_pct:+.2f}%",
                })
            df_pos = pd.DataFrame(rows)

            def _color_pnl(val):
                if isinstance(val, str):
                    return "color: green" if (val.startswith("$+") or val.startswith("+")) else "color: red" if "-" in val else ""
                if not isinstance(val, (int, float)):
                    return ""
                return "color: green" if val >= 0 else "color: red"

            try:
                styled = df_pos.style.map(_color_pnl, subset=["P&L $", "P&L %"])
            except AttributeError:
                styled = df_pos.style.applymap(_color_pnl, subset=["P&L $", "P&L %"])

            st.dataframe(styled, use_container_width=True, hide_index=True)
            st.caption(f"Total deployed: ${total_cost:,.0f} ({total_cost/equity*100:.1f}% of equity)" if equity > 0 else "")
    except Exception as e:
        st.error(f"Positions error: {e}")

with right:
    st.subheader("Risk Status")

    # Market status
    st.markdown(f"**Market:** :{mcolor}[{mstatus}]")

    # Circuit breaker (v7.31: scaled to tier max loss — warn at 50%, danger at 80%)
    st.markdown("**Circuit breaker:**")
    # Compute tier max loss first (used for thresholds + display)
    def _dashboard_max_loss(eq):
        _t = [
            (0,     1500,    150),  (1500,  2500,   250),
            (2500,  5000,    500),  (5000, 10000,  1000),
            (10000, 25000,  2000),  (25000, 50000, 3500),
            (50000, 75000,  5000),  (75000, 100000, 7500),
        ]
        for lo, hi, ml in _t:
            if lo <= eq < hi:
                return float(ml)
        if eq >= 100000:
            return max(10000.0, eq * 0.10)
        return 150.0
    _max_loss = _dashboard_max_loss(equity)
    _danger   = -_max_loss * 0.80
    _warn     = -_max_loss * 0.50
    if daily_pl < _danger:
        st.error(f"⛔ DANGER: ${daily_pl:+,.0f}")
    elif daily_pl < _warn:
        st.warning(f"⚠ Caution: ${daily_pl:+,.0f}")
    else:
        st.success("🟢 OK")

    st.markdown(f"**Daily goal:** ${DAILY_GOAL:,.0f} remaining")
    st.markdown(f"**Max loss limit:** ${_max_loss:,.0f}")

    st.divider()

    # v7.3: Margin utilization bar (replaces PDT as primary risk gate)
    st.markdown("**Margin Utilization**")
    try:
        # Pull from open positions we already have on the page
        if 'positions' in dir() and positions:
            total_notional = sum(
                abs(float(p.qty)) * float(p.current_price if hasattr(p, 'current_price') else p.avg_entry_price)
                for p in positions
            )
            util = total_notional / equity if equity > 0 else 0.0
            pct = util * 100
            if util >= 0.80:
                st.error(f"⛔ {pct:.1f}% (cap 80%)")
            elif util >= 0.70:
                st.warning(f"⚠ {pct:.1f}% (warn 70%)")
            else:
                st.success(f"🟢 {pct:.1f}%")
            st.progress(min(util, 1.0))
        else:
            st.caption("0.0% — no positions")
    except Exception:
        st.caption("unavailable")

    st.divider()

    # v7.31: Capital Tier Progression panel
    st.markdown("**Capital Tier**")
    try:
        # Inline tier logic (dashboard is stateless; reproduce the minimum
        # needed without importing the bot's src/ package)
        _TIERS = [
            (0,    1500,    "0 Seed",      0.040, 40,    200,    150),
            (1500, 2500,    "1 Sprout",    0.035, 70,    350,    250),
            (2500, 5000,    "2 Grow",      0.030, 110,   550,    500),
            (5000, 10000,   "3 Establish", 0.025, 190,   950,    1000),
            (10000, 25000,  "4 Build",     0.020, 350,   1750,   2000),
            (25000, 50000,  "5 Scale",     0.0175,650,   3250,   3500),
            (50000, 75000,  "6 Expand",    0.015, 940,   4700,   5000),
            (75000, 100000, "7 Pro",       0.0125,1090,  5450,   7500),
            (100000, None,  "8 Elite",     0.010, 1000,  5000,   10000),
        ]
        _eq = float(equity) if equity else 0.0
        _tier = None
        for lo, hi, name, rate, dly, wk, loss in _TIERS:
            if _eq >= lo and (hi is None or _eq < hi):
                _tier = (lo, hi, name, rate, dly, wk, loss)
                break
        if _tier is None:
            _tier = _TIERS[0]
        lo, hi, name, rate, dly, wk, loss = _tier
        # For Elite: daily/weekly target scales with equity
        if hi is None:
            dly = max(dly, _eq * rate)
            wk  = max(wk,  _eq * rate * 5)
            loss = max(loss, _eq * 0.10)

        st.markdown(f"**T{name}**")
        st.caption(f"Daily rate: {rate*100:.2f}%")
        c_a, c_b = st.columns(2)
        with c_a:
            st.caption(f"Daily target: ${dly:,.0f}")
            st.caption(f"Max loss: ${loss:,.0f}")
        with c_b:
            st.caption(f"Weekly target: ${wk:,.0f}")
            st.caption(f"Equity: ${_eq:,.0f}")

        # Progress to next tier
        if hi is not None:
            progress = (_eq - lo) / (hi - lo) if (hi - lo) > 0 else 0.0
            progress = max(0.0, min(1.0, progress))
            remaining = max(0.0, hi - _eq)
            next_name = _TIERS[min(_tier[0] // 1, len(_TIERS)-1)][2] if False else next(
                (n[2] for n in _TIERS if n[0] >= hi), "Elite")
            st.progress(progress)
            st.caption(f"Progress to next tier ({next_name}): ${remaining:,.0f} remaining")
        else:
            st.progress(1.0)
            st.caption("Top tier — scales with equity")
    except Exception as _te:
        st.caption(f"tier unavailable ({_te})")

    st.divider()

    # v7.3: PDT Mode indicator (replaces hardcoded 0/3 counter)
    st.markdown("**PDT Mode**")
    # Read from session trades for counter display
    try:
        sess_df = _fetch_session_trades()
        if not sess_df.empty and "entry_time" in sess_df.columns and "exit_time" in sess_df.columns:
            today_str = datetime.now().strftime("%Y-%m-%d")
            same_day = sess_df[
                sess_df["entry_time"].astype(str).str[:10] == sess_df.get("exit_time", sess_df["entry_time"]).astype(str).str[:10]
            ]
            pdt_count = len(same_day[same_day["entry_time"].astype(str).str[:10] == today_str])
        else:
            pdt_count = 0
    except Exception:
        pdt_count = 0

    # Mode indicator — config values are embedded in bot, dashboard shows post-SEC
    # elimination context so user knows the rule structure changed April 14, 2026
    st.info(
        "SEC eliminated PDT rule April 14, 2026. "
        "Bot runs in toggleable mode (Full Gate / Warn-Only / Disabled) "
        "controlled by PDT_ENABLED and PDT_WARN_ONLY in config.py."
    )
    st.caption(f"Today: {pdt_count} same-day round trips recorded")

    # v7.0: Swing hold mode
    st.markdown("**Exit Mode:**")
    st.success("🔄 Swing Hold (default)")

    # BTC Regime — reads btc_regime.txt pushed by bot to GitHub every scan
    st.markdown("**BTC Regime**")
    try:
        _repo  = st.secrets.get("GITHUB_REPO", "speedracer1186/Trading-Bot-Dashboard")
        _tok   = st.secrets.get("GITHUB_TOKEN", "")
        _hdrs  = {"Authorization": f"token {_tok}"} if _tok else {}
        _r     = requests.get(
            f"https://raw.githubusercontent.com/{_repo}/main/btc_regime.txt",
            headers=_hdrs, timeout=5)
        if _r.status_code == 200 and _r.text.strip():
            _rt = _r.text.strip()
            _ts = _rt.split("|")[-1].strip() if "|" in _rt else ""
            if "RANGING" in _rt:
                st.info(f"📊 RANGING  {_ts}")
            elif "TRENDING" in _rt:
                st.success(f"📈 TRENDING  {_ts}")
            else:
                st.warning(f"➡ NEUTRAL  {_ts}")
        else:
            st.caption("BTC/USD: monitoring (no regime data yet)")
    except Exception:
        st.caption("Regime: checking...")

    st.divider()

    # v7.0: Ticker list (all 31)
    st.markdown("**Tickers monitored**")
    tickers = [
        "PLTR","COIN","RBLX","SOFI","IONQ","HIMS","HOOD",
        "TSLA","MSTR",
        "GOOGL","AMZN","AMD","META","ORCL","APP","MSFT",
        "NVDA","SMH","AVGO","MU","ARM","MRVL","SMCI",
        "ARKK","TQQQ","SOXL",
        "XLF","XLE",
        "BTC/USD","IBIT"
    ]
    st.caption(", ".join(tickers))
    st.caption("ETH/USD: suspended (v7.0)")

    if is_paper:
        st.info("📄 PAPER MODE")
    else:
        st.error("🔴 LIVE MODE")

st.divider()

# ── Intraday equity chart ─────────────────────────────────────────────────────
st.subheader("Intraday Equity")
try:
    from alpaca.trading.requests import GetPortfolioHistoryRequest
    hist = client.get_portfolio_history(
        GetPortfolioHistoryRequest(period="1D", timeframe="1Min")
    )
    if hist and hist.equity:
        timestamps = [datetime.fromtimestamp(t) for t in hist.timestamp]
        equities   = hist.equity
        df_eq = pd.DataFrame({"Time": timestamps, "Equity": equities})
        df_eq = df_eq[df_eq["Equity"] > 0]
        if not df_eq.empty:
            st.line_chart(df_eq.set_index("Time")["Equity"])
        else:
            st.caption("No equity data for today yet.")
    else:
        st.caption("Equity history unavailable.")
except Exception as e:
    st.caption(f"Equity chart: {e}")

st.divider()

# ── Session trade log ─────────────────────────────────────────────────────────
st.subheader("Today's Session Trades")
try:
    df_trades = _fetch_session_trades()
    if df_trades.empty:
        st.info("No trade log pushed yet. Run tools/run_push_results.bat after session.")
    else:
        # Summary metrics
        has_pnl = "pnl" in df_trades.columns
        total_t   = len(df_trades)
        wins      = int((df_trades["pnl"] > 0).sum()) if has_pnl else 0
        total_pnl = float(df_trades["pnl"].sum())     if has_pnl else 0.0
        wr        = wins / total_t * 100 if total_t > 0 else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Trades",   total_t)
        m2.metric("Wins",     wins)
        m3.metric("Win Rate", f"{wr:.1f}%")
        m4.metric("P&L",      f"${total_pnl:+,.2f}")

        # v7.0: exit reason breakdown
        if "exit_reason" in df_trades.columns:
            er_counts = df_trades["exit_reason"].value_counts()
            reason_str = "  |  ".join(
                f"{r}: {c}" for r, c in er_counts.items()
            )
            st.caption(f"Exits — {reason_str}")

        # v7.0: highlight high-conviction trades (score >= 60)
        if "score" in df_trades.columns:
            high_conv = df_trades[df_trades["score"] >= 60]
            if not high_conv.empty:
                st.success(f"⭐ {len(high_conv)} high-conviction trade(s) today (score ≥ 60)")

        # Display table — v7.0 columns
        disp_cols = [c for c in [
            "symbol", "direction", "entry_time", "entry_price",
            "exit_price", "exit_reason", "pnl", "pnl_pct",
            "score", "tfs_agreed"
        ] if c in df_trades.columns]

        if has_pnl:
            def _color_pnl(val):
                if isinstance(val, str):
                    return "color: green" if (val.startswith("$+") or val.startswith("+")) else "color: red" if "-" in val else ""
                if not isinstance(val, (int, float)):
                    return ""
                return "color: green" if val >= 0 else "color: red"
            pnl_cols = [c for c in ["pnl", "pnl_pct"] if c in df_trades.columns]
            try:
                styled_trades = df_trades[disp_cols].style.map(_color_pnl, subset=pnl_cols)
            except AttributeError:
                styled_trades = df_trades[disp_cols].style.applymap(_color_pnl, subset=pnl_cols)
            st.dataframe(styled_trades, use_container_width=True, hide_index=True)
        else:
            st.dataframe(df_trades[disp_cols], use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"Trade log error: {e}")

st.divider()
st.caption(
    f"Trading Bot v7.0 | {'Paper' if is_paper else 'LIVE'} | "
    f"speedracer1186 | {datetime.now().strftime('%Y-%m-%d')}"
)
