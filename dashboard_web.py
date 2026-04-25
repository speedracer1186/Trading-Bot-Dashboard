"""
dashboard_web.py — Trading Bot v7.36 Web Dashboard
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
    page_title="Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Password gate ─────────────────────────────────────────────────────────────
def _check_password() -> bool:
    """v7.36 fix #2: refuse to load if DASHBOARD_PASSWORD is unset.
    Pre-v7.36 the gate defaulted to "changeme" silently, exposing
    public dashboards. Now an unset secret produces a hard error
    with setup instructions rather than a default-allow."""
    try:
        correct = st.secrets["DASHBOARD_PASSWORD"]
    except Exception:
        correct = None

    if not correct or correct == "changeme":
        st.error(
            "🔒 **DASHBOARD_PASSWORD secret is not set** "
            "(or is the insecure default 'changeme'). "
            "This dashboard refuses to load until a real password is "
            "configured."
        )
        st.info(
            "**To fix:** Go to share.streamlit.io → your app → "
            "⋮ Settings → Secrets and add a line like:  \n"
            "```\nDASHBOARD_PASSWORD = \"your-strong-password-here\"\n```\n"
            "Then click **Reboot app** from the ⋮ menu."
        )
        return False

    def _submit():
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
    """Fetch today's session trade CSV.

    v7.36 fix #1: retry GitHub fetch (8s timeout × 3 attempts with
    exponential backoff: 1s, 2s, 4s), then fall back to local file
    if dashboard is running on the same machine as the bot.
    Pre-v7.36 a single GitHub timeout meant blank dashboard.
    """
    import os, time as _t

    today = datetime.now().strftime("%Y%m%d")

    # ── Attempt 1-3: GitHub API with backoff ───────────────────────
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo  = st.secrets.get("GITHUB_REPO",  "speedracer1186/Trading-Bot-Dashboard")
        headers = {"Authorization": f"token {token}"} if token else {}
        url = f"https://api.github.com/repos/{repo}/contents/"

        backoff = [0, 1, 2]  # delay before each attempt: 0s, 1s, 2s
        last_err = None
        for delay in backoff:
            if delay > 0:
                _t.sleep(delay)
            try:
                resp = requests.get(url, headers=headers, timeout=8)
                if resp.status_code == 200:
                    files = [f["name"] for f in resp.json()
                             if f["name"].startswith(f"session_paper_{today}")
                             and f["name"].endswith(".csv")]
                    if files:
                        fname = sorted(files)[-1]
                        dl_url = f"https://raw.githubusercontent.com/{repo}/main/{fname}"
                        df = pd.read_csv(dl_url)
                        return df
                    # No file for today on GitHub yet — fall through to local
                    break
                last_err = f"HTTP {resp.status_code}"
            except Exception as e:
                last_err = str(e)
                continue
    except Exception:
        pass

    # ── Fallback: local file (works when dashboard runs alongside bot) ──
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        # tools/ → ../results/
        local = os.path.join(here, "..", "results", f"session_paper_{today}.csv")
        if os.path.exists(local):
            return pd.read_csv(local)
    except Exception:
        pass

    return pd.DataFrame()

# ── Auto-refresh (preserves login session state) ─────────────────────────────


def _fetch_recent_session_trades(days: int = 7) -> pd.DataFrame:
    """v7.36 fix #4: Fetch the last N days of session CSVs and concat
    into one frame for week-over-week comparison. Tries GitHub then
    falls back to local files. Adds a `session_date` column derived
    from the filename."""
    import os
    out = []
    today = datetime.now().date()

    # Try GitHub first (preferred — works for remote dashboard)
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo  = st.secrets.get("GITHUB_REPO",  "speedracer1186/Trading-Bot-Dashboard")
        headers = {"Authorization": f"token {token}"} if token else {}
        url = f"https://api.github.com/repos/{repo}/contents/"
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            files = sorted([
                f["name"] for f in resp.json()
                if f["name"].startswith("session_paper_")
                and f["name"].endswith(".csv")
            ])
            for fname in files[-days:]:
                try:
                    # Extract date from session_paper_YYYYMMDD.csv
                    date_str = fname.replace("session_paper_", "")[:8]
                    dl_url = f"https://raw.githubusercontent.com/{repo}/main/{fname}"
                    df = pd.read_csv(dl_url)
                    if not df.empty:
                        df["session_date"] = date_str
                        out.append(df)
                except Exception:
                    continue
    except Exception:
        pass

    # Fallback: local results/ directory
    if not out:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            results_dir = os.path.join(here, "..", "results")
            if os.path.isdir(results_dir):
                files = sorted([
                    f for f in os.listdir(results_dir)
                    if f.startswith("session_paper_") and f.endswith(".csv")
                ])
                for fname in files[-days:]:
                    try:
                        date_str = fname.replace("session_paper_", "")[:8]
                        df = pd.read_csv(os.path.join(results_dir, fname))
                        if not df.empty:
                            df["session_date"] = date_str
                            out.append(df)
                    except Exception:
                        continue
        except Exception:
            pass

    if out:
        return pd.concat(out, ignore_index=True)
    return pd.DataFrame()


def _bot_health_check() -> dict:
    """v7.36 fix #5: Inspect local log file (if accessible) for bot
    health signals. Returns a dict with last_log_ts, errors_last_hour,
    and a synthesized `status` (HEALTHY / STALE / ERRORS). When the
    dashboard runs remotely without log access, returns empty dict."""
    import os, re
    out = {"available": False, "status": "UNKNOWN", "errors_last_hour": 0,
           "last_log_ts": None, "warnings_last_hour": 0,
           "scans_last_hour": 0}
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(here, "..", "trading_bot.log")
        if not os.path.exists(log_path):
            return out
        out["available"] = True
        # Read last ~5000 lines to scan recent activity
        with open(log_path, "rb") as f:
            try:
                f.seek(-200_000, os.SEEK_END)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", errors="ignore")
        lines = tail.splitlines()
        ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        last_ts = None
        cutoff = datetime.now() - timedelta(hours=1)
        err = warn = scan = 0
        for ln in lines:
            m = ts_pattern.match(ln)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            last_ts = ts
            if ts >= cutoff:
                if "[ERROR]" in ln:
                    err += 1
                elif "[WARNING]" in ln:
                    warn += 1
                if "scan" in ln.lower() or "MTF SIGNAL" in ln:
                    scan += 1
        out["last_log_ts"] = last_ts
        out["errors_last_hour"] = err
        out["warnings_last_hour"] = warn
        out["scans_last_hour"] = scan
        # Status synthesis
        age_min = ((datetime.now() - last_ts).total_seconds() / 60
                   if last_ts else 9999)
        if last_ts is None:
            out["status"] = "NO_DATA"
        elif age_min > 5:
            out["status"] = "STALE"   # bot may have crashed
        elif err > 5:
            out["status"] = "ERRORS"
        elif err > 0 or warn > 10:
            out["status"] = "WARNINGS"
        else:
            out["status"] = "HEALTHY"
        out["age_minutes"] = age_min
    except Exception:
        pass
    return out


def _read_btc_regime() -> str:
    """v7.36 fix #6: Read crypto regime from data/btc_regime.txt
    if accessible (bot writes this each scan). Returns 'unknown' if
    not readable."""
    import os
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "data", "btc_regime.txt")
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read().strip()
    except Exception:
        pass
    return "unknown"


# ── Auto-refresh (preserves login session state) ─────────────────────────────
# Uses streamlit-autorefresh instead of meta http-equiv which wipes session state
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=30_000, key="dashboard_refresh")
except ImportError:
    # Fallback: show manual refresh button — do NOT use meta refresh (logs you out)
    if st.button("🔄 Refresh", key="manual_refresh"):
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD 2.0 — v7.36.3
# ─────────────────────────────────────────────────────────────────────────────
#  Designed for non-technical readers: every metric has a plain-English
#  explanation. Trading-jargon terms (Sharpe, profit factor, etc) live in
#  expandable tooltips under each metric. Replaces all deprecated
#  use_container_width=True with width="stretch" since use_container_width
#  was removed by Streamlit after 2025-12-31.
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_version() -> str:
    """Pull version dynamically from src/version.py so dashboard
    never goes stale relative to the bot it monitors."""
    import os, re
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        version_file = os.path.join(here, "..", "src", "version.py")
        if os.path.exists(version_file):
            with open(version_file) as f:
                content = f.read()
            m = re.search(r'VERSION_SHORT\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "v7.36.3"


def _compute_performance_metrics(trades_df: pd.DataFrame) -> dict:
    """Compute headline performance metrics from a trades dataframe.

    Returns a dict with the metrics safe non-traders can interpret with
    inline tooltips, computed only over CLOSED trades (rows with a
    non-zero pnl). Fields:
      total_trades, wins, losses, win_rate, total_pnl,
      avg_win, avg_loss, profit_factor, payoff_ratio, expectancy,
      sharpe, max_drawdown, max_drawdown_pct, current_streak

    All values are calibrated for an algorithmic bot rather than a
    discretionary trader, so e.g. Sharpe is computed from per-trade
    returns rather than per-day returns since a typical bot day has
    1-30 trades.
    """
    out = {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "profit_factor": 0.0, "payoff_ratio": 0.0, "expectancy": 0.0,
        "sharpe": 0.0, "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
        "current_streak": "—",
    }
    if trades_df is None or trades_df.empty or "pnl" not in trades_df.columns:
        return out

    closed = trades_df[trades_df["pnl"].fillna(0).astype(float) != 0].copy()
    closed["pnl"] = closed["pnl"].astype(float)
    if closed.empty:
        return out

    pnl = closed["pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    out["total_trades"] = int(len(closed))
    out["wins"] = int(len(wins))
    out["losses"] = int(len(losses))
    out["win_rate"] = (out["wins"] / out["total_trades"] * 100) if out["total_trades"] else 0.0
    out["total_pnl"] = float(pnl.sum())
    out["avg_win"] = float(wins.mean()) if not wins.empty else 0.0
    out["avg_loss"] = float(losses.mean()) if not losses.empty else 0.0  # negative

    # Profit factor: gross win $ / gross loss $.  >1 = profitable.
    gross_win = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = float(abs(losses.sum())) if not losses.empty else 0.0
    out["profit_factor"] = (gross_win / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0
    )
    # Payoff ratio: avg win / avg loss size.  >1 = winners bigger than losers.
    out["payoff_ratio"] = (out["avg_win"] / abs(out["avg_loss"])) if out["avg_loss"] != 0 else 0.0
    # Expectancy per trade (in $)
    out["expectancy"] = float(pnl.mean())

    # Sharpe per-trade: mean(pnl) / stdev(pnl). No risk-free rate adjustment
    # since per-trade horizon is too short for it to matter.
    if len(pnl) > 1 and pnl.std() > 0:
        out["sharpe"] = float(pnl.mean() / pnl.std())

    # Max drawdown: running peak of cumulative pnl, max gap below it
    cum = pnl.cumsum()
    running_max = cum.cummax()
    drawdown = cum - running_max
    out["max_drawdown"] = float(drawdown.min()) if not drawdown.empty else 0.0
    if running_max.max() > 0:
        out["max_drawdown_pct"] = float(out["max_drawdown"] / running_max.max() * 100)

    # Current streak (consecutive wins/losses at the tail)
    streak_n = 0
    streak_sign = None
    for v in reversed(pnl.tolist()):
        if v == 0:
            continue
        sign = "W" if v > 0 else "L"
        if streak_sign is None:
            streak_sign = sign
            streak_n = 1
        elif sign == streak_sign:
            streak_n += 1
        else:
            break
    if streak_sign:
        out["current_streak"] = f"{streak_n}{streak_sign}"
    return out


# ── Header (dynamic version, mode badge, market state) ───────────────────────
_VERSION = _resolve_version()
mode    = "🟡 PAPER" if is_paper else "🔴 LIVE"
mstatus = _market_status()
mcolor  = "green" if mstatus == "OPEN" else "orange" if "opens" in mstatus else "red"

st.markdown(
    f"# 📈 Trading Bot {_VERSION} &nbsp;&nbsp; {mode}"
)
st.caption(
    f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET  "
    f"·  Auto-refreshes every 30s  "
    f"·  Market: :{mcolor}[{mstatus}]"
)


# ── Bot health banner ────────────────────────────────────────────────────────
_health = _bot_health_check()
if _health.get("available"):
    _hcols = st.columns([2, 1, 1, 1])
    _status = _health["status"]
    _status_color = {
        "HEALTHY":  "green",  "WARNINGS": "orange", "ERRORS":   "red",
        "STALE":    "red",    "NO_DATA":  "gray",   "UNKNOWN":  "gray",
    }.get(_status, "gray")
    _last_ts = _health.get("last_log_ts")
    _age = _health.get("age_minutes", 0)
    with _hcols[0]:
        st.markdown(
            f"**Bot Status:** :{_status_color}[**{_status}**]  "
            f"·  Last log: {_last_ts.strftime('%H:%M:%S') if _last_ts else 'n/a'}  "
            f"({_age:.0f} min ago)"
        )
    _hcols[1].metric("Errors (1h)", _health.get("errors_last_hour", 0))
    _hcols[2].metric("Warnings (1h)", _health.get("warnings_last_hour", 0))
    _hcols[3].metric("Scans (1h)", _health.get("scans_last_hour", 0))
    if _status == "STALE":
        st.error(
            f"⚠️ Bot log hasn't been updated in {_age:.0f} minutes. "
            f"The bot may have crashed or hung — check the terminal."
        )
    elif _status == "ERRORS":
        st.warning(
            f"⚠️ {_health['errors_last_hour']} errors in the last hour — "
            f"check trading_bot.log for details."
        )
else:
    st.caption(
        "💡 Bot health panel unavailable (dashboard running remotely from bot). "
        "Run dashboard locally for log-based health checks."
    )


# ── Account summary (top-line numbers) ───────────────────────────────────────
daily_pl = 0.0
equity = 0.0
try:
    acct         = client.get_account()
    equity       = float(acct.equity)
    cash         = float(acct.cash)
    buying_pow   = float(acct.buying_power)
    last_equity  = float(getattr(acct, "last_equity", equity))
    daily_pl     = equity - last_equity
    daily_pl_pct = (daily_pl / last_equity * 100) if last_equity > 0 else 0.0

    # Daily goal lookup mirrors src/tier_manager.py
    def _dashboard_daily_goal(eq):
        _t = [
            (0,     1500,    40),    (1500,  2500,   70),
            (2500,  5000,   110),    (5000, 10000,  190),
            (10000, 25000,  350),    (25000, 50000, 650),
            (50000, 75000,  940),    (75000, 100000, 1090),
        ]
        for lo, hi, dly in _t:
            if lo <= eq < hi:
                return float(dly)
        if eq >= 100000:
            return max(1000.0, eq * 0.010)
        return 40.0
    DAILY_GOAL = _dashboard_daily_goal(equity)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Account Value",
              f"${equity:,.2f}",
              help="Total worth of your account = cash + market value of all positions.")
    c2.metric("Cash Available",
              f"${cash:,.2f}",
              help="Cash not currently invested. Used for new positions.")
    c3.metric("Buying Power",
              f"${buying_pow:,.2f}",
              help="How much you can spend on new positions right now (stocks "
                   "& options use buying power; crypto uses cash).")
    c4.metric("Today's P&L",
              f"${daily_pl:+,.2f}",
              f"{daily_pl_pct:+.2f}%",
              help="Profit or loss since yesterday's close. Green = up, red = down.")
    c5.metric("Goal Progress",
              f"{min(daily_pl / DAILY_GOAL * 100, 100):.0f}%" if DAILY_GOAL > 0 else "—",
              f"target ${DAILY_GOAL:,.0f}/day",
              delta_color="off",
              help="How close today's P&L is to today's profit goal. The goal "
                   "scales with your account size.")

    bar_pct = min(max(daily_pl / DAILY_GOAL, 0), 1.0) if DAILY_GOAL > 0 else 0
    st.progress(bar_pct, text=f"Daily goal: ${daily_pl:+,.2f} / ${DAILY_GOAL:,.0f}")

    # Drawdown alerts (preserved from v7.36)
    if equity > 0 and daily_pl < 0:
        _dl_pct = abs(daily_pl) / equity * 100
        if _dl_pct >= 8.0:
            st.error(
                f"🚨 **HARD-STOP THRESHOLD REACHED** — daily drawdown "
                f"-{_dl_pct:.2f}% (≥ 8%). Bot's risk-scaler should have "
                f"halted entries. Verify in log."
            )
        elif _dl_pct >= 6.0:
            st.warning(
                f"⚠️ **Risk-scaler soft-limit zone** — daily drawdown "
                f"-{_dl_pct:.2f}% (≥ 6%). Entry sizes reduced to 50%."
            )
        elif _dl_pct >= 3.0:
            st.info(
                f"📉 Drawdown caution: daily P&L -{_dl_pct:.2f}% "
                f"(approaching 6% soft-limit)."
            )
except Exception as e:
    st.error(f"Account error: {e}")
    st.caption("Check Streamlit secrets and Alpaca API key validity.")

st.divider()


# ── Performance metrics (NEW — Dashboard 2.0 headline panel) ─────────────────
st.subheader("📊 Performance Metrics")
st.caption(
    "How well the bot is trading, computed across the last 7 days of "
    "completed trades. Hover ❓ on any metric for what it means."
)

try:
    df_recent_trades = _fetch_recent_session_trades(days=7)
    metrics = _compute_performance_metrics(df_recent_trades)

    if metrics["total_trades"] == 0:
        st.info(
            "💡 No completed trades yet in the last 7 days. Metrics will populate "
            "as the bot trades. Open positions don't count — only closed trades."
        )
    else:
        # Row 1 — primary "is the bot working?" metrics
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(
            "Total Trades",
            f"{metrics['total_trades']}",
            f"{metrics['wins']}W / {metrics['losses']}L",
            delta_color="off",
            help="How many trades were closed in the last 7 days. "
                 "Wins/losses shown below."
        )
        # Win rate with green/red color cue
        wr = metrics["win_rate"]
        wr_delta_color = "normal" if wr >= 50 else "inverse"
        m2.metric(
            "Win Rate",
            f"{wr:.1f}%",
            f"{'above' if wr >= 50 else 'below'} 50%",
            delta_color=wr_delta_color,
            help="Percentage of trades that were profitable. Above 50% means "
                 "more wins than losses — but doesn't tell the whole story; "
                 "see Profit Factor."
        )
        m3.metric(
            "Total P&L (7d)",
            f"${metrics['total_pnl']:+,.2f}",
            help="Sum of all closed-trade profits and losses over the last "
                 "7 calendar days."
        )
        m4.metric(
            "Avg Per Trade",
            f"${metrics['expectancy']:+,.2f}",
            help="Average dollar P&L per closed trade. This is your 'edge' "
                 "on each trade. If this is positive long-term, the bot is "
                 "profitable."
        )
        m5.metric(
            "Streak",
            metrics["current_streak"],
            help="Current consecutive wins (W) or losses (L). Long losing "
                 "streaks may be normal — what matters is the average."
        )

        # Row 2 — risk-adjusted / quality metrics
        st.markdown("##### Risk-Adjusted Quality")
        q1, q2, q3, q4, q5 = st.columns(5)

        # Profit Factor: handle inf
        pf = metrics["profit_factor"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        pf_label = (
            "🟢 Excellent" if pf >= 2.0
            else "🟢 Good" if pf >= 1.5
            else "🟡 Marginal" if pf >= 1.0
            else "🔴 Losing"
        )
        q1.metric(
            "Profit Factor", pf_str, pf_label,
            delta_color="off",
            help="Total $ won ÷ total $ lost. Above 1.0 = profitable; "
                 "above 1.5 = good; above 2.0 = excellent. This handles "
                 "uneven win/loss sizes that win rate misses."
        )

        # Sharpe per-trade
        sharpe = metrics["sharpe"]
        sharpe_label = (
            "🟢 Excellent" if sharpe >= 0.5
            else "🟢 Good" if sharpe >= 0.25
            else "🟡 Marginal" if sharpe >= 0.0
            else "🔴 Negative"
        )
        q2.metric(
            "Sharpe (per-trade)",
            f"{sharpe:+.2f}",
            sharpe_label,
            delta_color="off",
            help="Average P&L divided by P&L volatility. Higher = more "
                 "consistent results. For per-trade Sharpe: above 0.25 is "
                 "good, above 0.5 is excellent. (Note: this is calculated "
                 "per-trade for an algo bot, not annualized like in funds.)"
        )

        # Payoff ratio
        po = metrics["payoff_ratio"]
        po_label = (
            "🟢 Good" if po >= 1.5
            else "🟡 OK" if po >= 1.0
            else "🔴 Risky"
        )
        q3.metric(
            "Payoff Ratio",
            f"{po:.2f}x",
            po_label,
            delta_color="off",
            help="Average win size ÷ average loss size. 1.5x means winners "
                 "are 50% bigger than losers. With low win rates you need "
                 "high payoff ratio; with high win rates you can survive lower."
        )

        # Avg win / avg loss
        q4.metric(
            "Avg Win",
            f"${metrics['avg_win']:+,.2f}",
            help="Average profit on winning trades. Compare to Avg Loss to "
                 "see if winners are bigger than losers (you want them to be)."
        )
        q5.metric(
            "Avg Loss",
            f"${metrics['avg_loss']:+,.2f}",
            help="Average loss on losing trades. Should be smaller in absolute "
                 "value than Avg Win for a healthy strategy."
        )

        # Drawdown row
        st.markdown("##### Drawdown")
        d1, d2, d3 = st.columns([1, 1, 2])
        d1.metric(
            "Max Drawdown ($)",
            f"${metrics['max_drawdown']:,.2f}",
            help="Largest peak-to-valley drop the bot's cumulative P&L "
                 "experienced. Lower (less negative) is better. This tells "
                 "you the worst-case streak you've actually lived through."
        )
        d2.metric(
            "Max Drawdown (%)",
            f"{metrics['max_drawdown_pct']:.1f}%",
            help="Same as above but as a percent of the prior peak. "
                 "Above 20% is concerning; above 30% suggests a strategy "
                 "review is needed."
        )
        with d3:
            st.markdown(
                f"**What this means in plain English:** "
                f"Your bot has been right "
                f"{metrics['win_rate']:.0f}% of the time, and on average each "
                f"trade adds **${metrics['expectancy']:+,.2f}** to your account. "
                f"For every $1 it loses on bad trades, it makes "
                f"**${metrics['profit_factor']:.2f}** on good ones. "
            )
except Exception as e:
    st.caption(f"Performance metrics unavailable: {e}")

st.divider()


# ── Equity curve chart (NEW) ─────────────────────────────────────────────────
st.subheader("📈 Equity Curve — last 7 days")
st.caption(
    "Cumulative profit & loss over time across all closed trades. "
    "An upward-sloping line means the bot is making money over time."
)

try:
    if not df_recent_trades.empty and "pnl" in df_recent_trades.columns:
        df_curve = df_recent_trades[df_recent_trades["pnl"].fillna(0).astype(float) != 0].copy()
        if not df_curve.empty:
            df_curve["pnl"] = df_curve["pnl"].astype(float)

            # Build x-axis: prefer timestamp column, fallback to index
            time_col = None
            for cand in ("entry_time", "timestamp", "datetime", "exit_time"):
                if cand in df_curve.columns:
                    time_col = cand
                    break
            if time_col:
                try:
                    df_curve[time_col] = pd.to_datetime(df_curve[time_col])
                    df_curve = df_curve.sort_values(time_col)
                except Exception:
                    pass

            df_curve["cumulative_pnl"] = df_curve["pnl"].cumsum()
            chart_df = df_curve[[time_col or df_curve.columns[0], "cumulative_pnl"]].copy() \
                       if time_col else df_curve[["cumulative_pnl"]].copy()
            if time_col:
                chart_df = chart_df.set_index(time_col)
            st.line_chart(chart_df, height=300)
        else:
            st.info("No closed trades yet to chart.")
    else:
        st.info("Equity curve will appear once trades are completed.")
except Exception as e:
    st.caption(f"Equity curve error: {e}")

st.divider()


# ── Strategy breakdown (NEW) ─────────────────────────────────────────────────
st.subheader("🎯 Strategy Breakdown")
st.caption(
    "Where the bot's recent profits & losses came from. Helps spot which "
    "strategies are working and which are dragging."
)

try:
    if not df_recent_trades.empty:
        # Try to identify strategy by exit_reason or strategy column
        df_strat = df_recent_trades[df_recent_trades["pnl"].fillna(0).astype(float) != 0].copy()
        df_strat["pnl"] = df_strat["pnl"].astype(float)

        # Heuristic: derive strategy bucket from exit_reason or strategy field
        def _strategy_bucket(row):
            er = str(row.get("exit_reason", "")).lower()
            sym = str(row.get("symbol", ""))
            if er.startswith("scalp_"):
                return "Crypto Scalp"
            if "/" in sym:
                return "Crypto Hybrid"
            if len(sym) > 6 and any(c.isdigit() for c in sym):
                return "Options"
            return "Shares"

        df_strat["strategy_bucket"] = df_strat.apply(_strategy_bucket, axis=1)
        agg = df_strat.groupby("strategy_bucket").agg(
            trades=("pnl", "count"),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            wins=("pnl", lambda s: int((s > 0).sum())),
            losses=("pnl", lambda s: int((s < 0).sum())),
        ).round(2).reset_index()
        agg["win_rate_%"] = (agg["wins"] / agg["trades"] * 100).round(1)
        agg = agg.sort_values("total_pnl", ascending=False)

        s1, s2 = st.columns([2, 1])
        with s1:
            st.dataframe(
                agg.rename(columns={
                    "strategy_bucket": "Strategy",
                    "trades": "Trades",
                    "total_pnl": "Total P&L ($)",
                    "avg_pnl": "Avg ($)",
                    "wins": "W",
                    "losses": "L",
                    "win_rate_%": "Win %",
                }),
                width="stretch",
                hide_index=True,
            )
        with s2:
            chart_data = agg.set_index("strategy_bucket")["total_pnl"]
            st.bar_chart(chart_data, height=200)
            st.caption("Total P&L by strategy")
    else:
        st.info("Strategy breakdown will appear once trades are completed.")
except Exception as e:
    st.caption(f"Strategy breakdown error: {e}")

st.divider()


# ── Open positions ────────────────────────────────────────────────────────────
left, right = st.columns([3, 1])

with left:
    st.subheader("Open Positions")
    try:
        positions = client.get_all_positions()
        if not positions:
            st.info("📭 No open positions right now. The bot will open new "
                    "ones when it finds high-probability setups.")
        else:
            rows = []
            total_cost = 0.0
            for p in positions:
                raw_qty = float(p.qty)
                entry = float(p.avg_entry_price)
                current = float(p.current_price)
                _asset_class = str(getattr(p, "asset_class", "") or "").lower()
                _is_option = (_asset_class == "us_option")
                _is_crypto = (_asset_class == "crypto") or ("/" in str(p.symbol))
                if _is_option:
                    cost = abs(raw_qty) * entry * 100.0
                else:
                    cost = abs(raw_qty) * entry
                unreal = float(p.unrealized_pl)
                unr_pct = float(p.unrealized_plpc) * 100
                total_cost += cost
                # Format qty: fractional for crypto, int otherwise
                qty_str = f"{raw_qty:.4f}" if _is_crypto else str(int(raw_qty))
                rows.append({
                    "Symbol": p.symbol,
                    "Type": "🪙 Crypto" if _is_crypto else ("📊 Option" if _is_option else "📈 Stock"),
                    "Qty": qty_str,
                    "Entry": f"${entry:.2f}",
                    "Current": f"${current:.2f}",
                    "Cost": f"${cost:,.0f}",
                    "Deployed %": f"{(cost / equity * 100) if equity else 0:.1f}%",
                    "P&L $": f"${unreal:+,.2f}",
                    "P&L %": f"{unr_pct:+.2f}%",
                })
            df_pos = pd.DataFrame(rows)

            # Color-style P&L columns
            def _color_pnl(val):
                try:
                    v = float(str(val).replace("$", "").replace(",", "").replace("%", "").replace("+", ""))
                    return "color: #28a745" if v > 0 else ("color: #dc3545" if v < 0 else "")
                except Exception:
                    return ""

            styled = df_pos.style.map(_color_pnl, subset=["P&L $", "P&L %"])
            st.dataframe(styled, width="stretch", hide_index=True)
            st.caption(
                f"Total deployed: **${total_cost:,.0f}** "
                f"({(total_cost / equity * 100) if equity else 0:.1f}% of account)"
            )
    except Exception as e:
        st.error(f"Position fetch error: {e}")

with right:
    st.subheader("Risk Status")

    # Market state
    if mstatus == "OPEN":
        st.success(f"📈 Market: **OPEN**")
    else:
        st.warning(f"⏰ Market: {mstatus}")

    # Circuit breaker (heuristic from health log)
    if _health.get("status") in ("ERRORS", "STALE"):
        st.error("Circuit breaker:\n\n🔴 **CHECK BOT**")
    else:
        st.success("Circuit breaker:\n\n🟢 **OK**")

    # Daily-goal remaining
    try:
        if DAILY_GOAL > 0:
            remaining = max(0, DAILY_GOAL - daily_pl)
            st.markdown(f"**Daily goal:** ${remaining:.0f} remaining")
    except Exception:
        pass

    # Max-loss limit (mirrors tier_manager)
    try:
        def _dashboard_max_loss(eq):
            _t = [
                (0, 1500, 150),     (1500, 2500, 250),
                (2500, 5000, 500),  (5000, 10000, 1000),
                (10000, 25000, 2000), (25000, 50000, 4000),
                (50000, 75000, 5500), (75000, 100000, 7500),
            ]
            for lo, hi, ml in _t:
                if lo <= eq < hi:
                    return float(ml)
            return max(7500.0, eq * 0.075)
        MAX_LOSS = _dashboard_max_loss(equity)
        st.markdown(f"**Max loss limit:** ${MAX_LOSS:,.0f}")
    except Exception:
        pass

    # BTC regime
    _regime = _read_btc_regime()
    if _regime != "unknown":
        _regime_display = _regime.split(" | ", 1)[0].strip() if " | " in _regime else _regime
        _regime_emoji = {"RANGING": "↔️", "TRENDING": "📈", "NEUTRAL": "⚖️"}.get(
            _regime_display.upper(), "❓")
        st.markdown(f"**BTC regime:** {_regime_emoji} `{_regime_display}`")

st.divider()


# ── Recent session trades ─────────────────────────────────────────────────────
st.subheader("📋 Recent Trades")
st.caption("Last 30 trade entries from the last 7 days, newest first.")

try:
    if not df_recent_trades.empty:
        # Show most useful columns
        show_cols = [c for c in (
            "session_date", "symbol", "direction", "qty",
            "entry_price", "exit_price", "pnl", "pnl_pct", "exit_reason"
        ) if c in df_recent_trades.columns]
        if show_cols:
            df_show = df_recent_trades[show_cols].copy()
            # Reverse so newest is first
            df_show = df_show.iloc[::-1].head(30)

            def _color_pnl_val(val):
                try:
                    v = float(val)
                    return "color: #28a745" if v > 0 else ("color: #dc3545" if v < 0 else "")
                except Exception:
                    return ""

            pnl_cols = [c for c in ("pnl", "pnl_pct") if c in df_show.columns]
            styled_show = df_show.style.map(_color_pnl_val, subset=pnl_cols) if pnl_cols else df_show
            st.dataframe(styled_show, width="stretch", hide_index=True)
        else:
            st.dataframe(df_recent_trades.head(30), width="stretch", hide_index=True)
    else:
        st.info("No trades yet — bot will start populating this once it trades.")
except Exception as e:
    st.caption(f"Recent trades error: {e}")

st.divider()


# ── Footer ───────────────────────────────────────────────────────────────────
st.caption(
    f"Trading Bot {_VERSION}  |  "
    f"{'Paper trading' if is_paper else 'LIVE TRADING'}  |  "
    f"speedracer1186  |  "
    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)
