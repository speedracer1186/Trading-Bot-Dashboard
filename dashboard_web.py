"""
dashboard_web.py — Trading Bot Web Dashboard
=============================================
Password-gated Streamlit dashboard. Connects to Alpaca paper (or live)
account via API keys stored in Streamlit secrets — never in code or GitHub.

Deployment: Streamlit Cloud (free tier)
  https://share.streamlit.io → connect private GitHub repo → set secrets

Required secrets (set in Streamlit Cloud dashboard → App settings → Secrets):
    DASHBOARD_PASSWORD = "your-chosen-password"
    ALPACA_API_KEY     = "your-alpaca-api-key"
    ALPACA_SECRET_KEY  = "your-alpaca-secret-key"
    ALPACA_PAPER       = "true"          # "false" for live account
    GITHUB_TOKEN       = "ghp_..."       # Personal Access Token (repo scope)
    GITHUB_REPO        = "username/trading-dashboard"  # your private repo

Features:
  - Account summary (equity, cash, buying power, daily P&L)
  - Open positions table (qty, entry, cost basis, deployed %, P&L)
  - Today's closed trades (from Alpaca order history)
  - Equity curve since session start
  - Risk status (circuit breaker, daily goal, market status)
  - Recent session CSV data from results/ folder (if available)
  - Auto-refreshes every 30 seconds
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────
#  PAGE CONFIG  (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────
#  PASSWORD GATE
# ─────────────────────────────────────────────────────────────────
def _check_password() -> bool:
    def _submitted():
        try:
            correct = st.secrets["DASHBOARD_PASSWORD"]
        except Exception:
            correct = "changeme"
        if st.session_state.get("pw_input") == correct:
            st.session_state["authenticated"] = True
        else:
            st.session_state["authenticated"] = False
            st.session_state["pw_wrong"]       = True

    if st.session_state.get("authenticated"):
        return True

    st.markdown("## 📈 Trading Bot Dashboard")
    st.markdown("Enter the dashboard password to continue.")
    st.text_input("Password", type="password",
                  key="pw_input", on_change=_submitted)
    if st.session_state.get("pw_wrong"):
        st.error("Incorrect password — try again.")
    st.stop()
    return False


_check_password()

# ─────────────────────────────────────────────────────────────────
#  ALPACA CONNECTION  (cached — one client per session)
# ─────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Connecting to Alpaca…")
def _get_client():
    try:
        from alpaca.trading.client import TradingClient
        key    = st.secrets["ALPACA_API_KEY"]
        secret = st.secrets["ALPACA_SECRET_KEY"]
        paper  = str(st.secrets.get("ALPACA_PAPER", "true")).lower() == "true"
        return TradingClient(key, secret, paper=paper), paper
    except Exception as e:
        st.error(f"Could not connect to Alpaca: {e}")
        st.stop()


client, is_paper = _get_client()

# ─────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────
def _pct_color(val: float) -> str:
    """Return a coloured string for display in st.markdown."""
    colour = "green" if val >= 0 else "red"
    sign   = "+" if val >= 0 else ""
    return f":{colour}[{sign}{val:.2f}%]"


def _dollar_color(val: float) -> str:
    colour = "green" if val >= 0 else "red"
    sign   = "+" if val >= 0 else ""
    return f":{colour}[{sign}${abs(val):,.2f}]"


def _market_status() -> str:
    """Return OPEN / PRE-MARKET / AFTER-HOURS / CLOSED."""
    try:
        clock = client.get_clock()
        return "OPEN" if clock.is_open else "CLOSED"
    except Exception:
        now_et = datetime.now(timezone.utc) - timedelta(hours=4)
        h, m   = now_et.hour, now_et.minute
        mins   = h * 60 + m
        if 570 <= mins < 960:   # 9:30–16:00 ET
            return "OPEN"
        if 540 <= mins < 570:   # 9:00–9:30 ET
            return "PRE-MARKET"
        if 960 <= mins < 1200:  # 16:00–20:00 ET
            return "AFTER-HOURS"
        return "CLOSED"


# ─────────────────────────────────────────────────────────────────
#  HEADER
# ─────────────────────────────────────────────────────────────────
mode_badge = "🟡 PAPER" if is_paper else "🔴 LIVE"
st.markdown(
    f"## 📈 Trading Bot v6.7.0 — Live Dashboard &nbsp;&nbsp; {mode_badge}"
)
st.caption(
    f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET  "
    f"· Auto-refreshes every 30 s"
)

# ─────────────────────────────────────────────────────────────────
#  ACCOUNT SUMMARY
# ─────────────────────────────────────────────────────────────────
# Safe defaults — used by Risk Status section even if account call fails
daily_pl   = 0.0
DAILY_GOAL = 2_000.0
DAILY_MAX  = 1_000.0

try:
    account       = client.get_account()
    equity        = float(account.equity)
    cash          = float(account.cash)
    buying_power  = float(account.buying_power)
    last_equity   = float(getattr(account, "last_equity", equity))
    daily_pl      = equity - last_equity
    daily_pl_pct  = (daily_pl / last_equity * 100) if last_equity > 0 else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity",        f"${equity:,.2f}")
    c2.metric("Cash",          f"${cash:,.2f}")
    c3.metric("Buying Power",  f"${buying_power:,.2f}")
    c4.metric(
        "Daily P&L",
        f"${daily_pl:+,.2f}",
        f"{daily_pl_pct:+.2f}%",
        delta_color="normal",
    )
    c5.metric(
        "Goal Progress",
        f"{min(daily_pl / DAILY_GOAL * 100, 100):.0f}%",
        f"target ${DAILY_GOAL:,.0f}/day",
        delta_color="off",
    )

    # Daily P&L progress bar
    bar_pct = min(max(daily_pl / DAILY_GOAL, 0), 1.0) if DAILY_GOAL > 0 else 0
    st.progress(bar_pct, text=f"Daily goal: ${daily_pl:+,.2f} / ${DAILY_GOAL:,.0f}")

except Exception as e:
    st.error(f"Account error: {e}")

st.divider()

# ─────────────────────────────────────────────────────────────────
#  TWO-COLUMN LAYOUT: POSITIONS | RISK STATUS
# ─────────────────────────────────────────────────────────────────
left, right = st.columns([3, 1])

with left:
    st.subheader("Open Positions")
    try:
        positions = client.get_all_positions()
        if not positions:
            st.info("No open positions.")
        else:
            rows          = []
            total_cost    = 0.0
            total_unreal  = 0.0

            for p in positions:
                qty        = int(float(p.qty))
                entry      = float(p.avg_entry_price)
                current    = float(p.current_price)
                cost       = abs(qty) * entry
                unreal     = float(p.unrealized_pl)
                unreal_pct = float(p.unrealized_plpc) * 100
                total_cost   += cost
                total_unreal += unreal
                rows.append({
                    "Symbol":   p.symbol,
                    "Qty":      qty,
                    "Entry $":  round(entry, 2),
                    "Current $":round(current, 2),
                    "Cost":     f"${cost:,.0f}",
                    "Deployed": f"{cost/equity*100:.1f}%",
                    "P&L $":    round(unreal, 2),
                    "P&L %":    round(unreal_pct, 2),
                })

            df_pos = pd.DataFrame(rows)

            def _color_pnl(val):
                try:
                    v = float(val)
                    c = "background-color: #1a3a1a; color: #4ade80" if v >= 0 \
                        else "background-color: #3a1a1a; color: #f87171"
                    return c
                except Exception:
                    return ""

            styled = (
                df_pos.style
                .map(_color_pnl, subset=["P&L $", "P&L %"])
                .format({"P&L $": "{:+.2f}", "P&L %": "{:+.2f}%"})
            )
            st.dataframe(styled, hide_index=True)
            st.caption(
                f"Total deployed: **${total_cost:,.0f}**"
                f" ({total_cost/equity*100:.1f}% of equity)  ·  "
                f"Unrealised P&L: **${total_unreal:+,.2f}**"
            )
    except Exception as e:
        st.error(f"Positions error: {e}")

with right:
    st.subheader("Risk Status")
    mkt = _market_status()
    mkt_color = "green" if mkt == "OPEN" else \
                "orange" if "MARKET" in mkt or "HOURS" in mkt else "red"
    st.markdown(f"**Market:** :{mkt_color}[{mkt}]")

    cb_ok = daily_pl > -DAILY_MAX
    st.markdown(
        f"**Circuit breaker:** {'🟢 OK' if cb_ok else '🔴 TRIGGERED'}"
    )
    goal_hit = daily_pl >= DAILY_GOAL
    st.markdown(
        f"**Daily goal:** {'🎯 HIT' if goal_hit else f'${DAILY_GOAL-daily_pl:,.0f} remaining'}"
    )
    st.markdown(
        f"**Max loss limit:** ${DAILY_MAX:,.0f}"
    )

    st.divider()
    st.caption("**Tickers monitored**")
    tickers = [
        "PLTR","COIN","RBLX","SOFI","IONQ","HIMS",
        "TSLA","MSTR","GOOGL","AMZN","AMD","META","ORCL","APP",
        "NVDA","SMH","AVGO","MU","ARM","MRVL",
        "ARKK","TQQQ","SOXL",
        "BTC/USD","ETH/USD","IBIT",
    ]
    st.caption(", ".join(tickers))

st.divider()

# ─────────────────────────────────────────────────────────────────
#  TODAY'S CLOSED TRADES  (from Alpaca order history)
# ─────────────────────────────────────────────────────────────────
st.subheader("Today's Closed Trades")
try:
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums    import QueryOrderStatus

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    req    = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        after=today_start,
        limit=50,
    )
    orders = client.get_orders(req)

    if not orders:
        st.info("No closed trades today.")
    else:
        rows = []
        for o in orders:
            filled_qty = float(o.filled_qty or 0)
            if filled_qty == 0:
                continue
            fill_price = float(o.filled_avg_price or 0)
            side       = str(o.side).replace("OrderSide.", "").replace("SideType.", "")
            rows.append({
                "Time":     str(o.filled_at or o.submitted_at or "")[:16]
                              .replace("T", " ").replace("+00:00", ""),
                "Symbol":   o.symbol,
                "Side":     side.upper(),
                "Qty":      int(filled_qty),
                "Fill $":   round(fill_price, 2),
                "Notional": f"${filled_qty * fill_price:,.0f}",
                "Status":   str(o.status).replace("OrderStatus.", ""),
            })

        if rows:
            df_ord = pd.DataFrame(rows)
            def _color_side(val):
                return "color: #4ade80" if val == "BUY" else "color: #f87171"
            styled_ord = df_ord.style.map(_color_side, subset=["Side"])
            st.dataframe(styled_ord, hide_index=True)
            st.caption(f"{len(rows)} fills today")
        else:
            st.info("No filled orders today.")
except Exception as e:
    st.error(f"Orders error: {e}")

st.divider()

st.divider()

# ─────────────────────────────────────────────────────────────────
#  SESSION TRADE LOG  (read from GitHub private repo via API)
# ─────────────────────────────────────────────────────────────────
st.subheader("Session Trade Log")

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_github_csv(repo: str, filename: str, token: str = ""):
    """
    Fetch a session CSV from GitHub.
    Tries raw URL first (works for public repos, no auth needed).
    Falls back to Contents API with token (works for private repos).
    """
    import base64, urllib.request as _ur, json as _json
    from io import StringIO

    # Method 1: raw URL (public repo — no token needed)
    raw_url = (f"https://raw.githubusercontent.com/"
               f"{repo}/main/results/{filename}")
    try:
        req = _ur.Request(raw_url, headers={"User-Agent": "TradingBotDashboard/6.5"})
        with _ur.urlopen(req, timeout=8) as r:
            df = pd.read_csv(StringIO(r.read().decode("utf-8")))
            return df if not df.empty else None
    except _ur.HTTPError as e:
        if e.code != 404:
            pass  # fall through to method 2
    except Exception:
        pass

    # Method 2: Contents API with token (private repo)
    if not token:
        return None
    api_url = (f"https://api.github.com/repos/{repo}"
               f"/contents/results/{filename}")
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github.v3+json",
        "User-Agent":    "TradingBotDashboard/6.5",
    }
    try:
        req = _ur.Request(api_url, headers=headers)
        with _ur.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
        raw  = base64.b64decode(data["content"]).decode("utf-8")
        df   = pd.read_csv(StringIO(raw))
        return df if not df.empty else None
    except _ur.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


_today_str  = datetime.now().strftime("%Y%m%d")
_gh_token   = st.secrets.get("GITHUB_TOKEN", "")
_gh_repo    = st.secrets.get("GITHUB_REPO",  "")
_log_loaded = False

if _gh_repo:
    for _mode in ("paper", "live"):
        _fname = f"session_{_mode}_{_today_str}.csv"
        try:
            _df_gh = _fetch_github_csv(_gh_repo, _fname, token=_gh_token)
            if _df_gh is not None:
                st.dataframe(_df_gh, hide_index=True)
                _log_loaded = True
                st.caption(
                    f"Source: GitHub `{_gh_repo}/results/{_fname}` "
                    f"· {len(_df_gh)} trades · auto-pushed by bot"
                )
                break
        except Exception as _ge:
            st.caption(f"GitHub fetch error: {_ge}")

if not _log_loaded:
    if not _gh_repo:
        st.info(
            "Add `GITHUB_REPO` to your Streamlit secrets "
            "(e.g. `speedracer1186/Trading-Bot-Dashboard`), "
            "then run `run_push_results.bat` after each trading session."
        )
    else:
        st.info(
            f"No trade log pushed yet for today ({_today_str}). "
            "Run `run_push_results.bat` after the trading session ends "
            "and it will appear here automatically."
        )


# ─────────────────────────────────────────────────────────────────
#  INTRADAY EQUITY CURVE  (approximated from account history)
# ─────────────────────────────────────────────────────────────────
st.subheader("Intraday Equity Snapshot")
try:
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    req_hist = GetPortfolioHistoryRequest(
        period="1D",
        timeframe="5Min",
        extended_hours=False,
    )
    hist = client.get_portfolio_history(req_hist)
    if hist and hist.equity:
        times  = [
            datetime.fromtimestamp(t).strftime("%H:%M")
            for t in hist.timestamp
        ]
        eq_vals = [float(v) for v in hist.equity]
        df_eq   = pd.DataFrame({"Time": times, "Equity": eq_vals})
        df_eq   = df_eq[df_eq["Equity"] > 0]
        if not df_eq.empty:
            st.line_chart(
                df_eq.set_index("Time")["Equity"]
            )
            start_val = df_eq["Equity"].iloc[0]
            end_val   = df_eq["Equity"].iloc[-1]
            session_return = (end_val - start_val) / start_val * 100
            st.caption(
                f"Session: ${start_val:,.2f} → ${end_val:,.2f}  "
                f"({session_return:+.2f}%)"
            )
    else:
        st.info("No equity history available yet — check back after market open.")
except Exception as e:
    st.info(f"Equity curve not available: {e}")

# ─────────────────────────────────────────────────────────────────
#  FOOTER + AUTO-REFRESH
# ─────────────────────────────────────────────────────────────────
st.divider()
col_a, col_b = st.columns([4, 1])
col_a.caption(
    "Trading Bot v6.7.0 · Alpaca paper trading · "
    "Data refreshes every 30 seconds · "
    "For monitoring only — no orders placed from this dashboard."
)
if col_b.button("🔄 Refresh now"):
    st.rerun()

# Auto-refresh every 30 seconds using session state counter
if "refresh_count" not in st.session_state:
    st.session_state.refresh_count = 0
time.sleep(30)
st.session_state.refresh_count += 1
st.rerun()
