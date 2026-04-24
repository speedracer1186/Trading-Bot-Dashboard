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
    page_title="Trading Bot v7.36",
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

# ── Header ────────────────────────────────────────────────────────────────────
mode    = "🟡 PAPER" if is_paper else "🔴 LIVE"
mstatus = _market_status()
mcolor  = "green" if mstatus == "OPEN" else "orange" if "opens" in mstatus else "red"

st.markdown(f"# 📈 Trading Bot v7.36 &nbsp;&nbsp; {mode}")
st.caption(
    f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET  "
    f"· Auto-refreshes every 30s  "
    f"· Market: :{mcolor}[{mstatus}]"
)

# ── Bot health banner (v7.36 fix #5) ─────────────────────────────────────────
_health = _bot_health_check()
if _health.get("available"):
    _hcols = st.columns([2, 1, 1, 1])
    _status = _health["status"]
    _status_color = {
        "HEALTHY":  "green",
        "WARNINGS": "orange",
        "ERRORS":   "red",
        "STALE":    "red",
        "NO_DATA":  "gray",
        "UNKNOWN":  "gray",
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
            f"The bot may have crashed or hung. Check the terminal."
        )
    elif _status == "ERRORS":
        st.warning(
            f"⚠️ {_health['errors_last_hour']} errors in the last hour — "
            f"check trading_bot.log for details."
        )
else:
    # Dashboard running remotely without log access — show a hint
    st.caption(
        "💡 Bot health panel unavailable (dashboard running remotely). "
        "Run dashboard locally for log-based health checks."
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

    # v7.36 fix #7: risk alerting — visible drawdown warnings.
    # Pre-v7.36 the dashboard only showed the goal-progress bar; users
    # had to mentally calculate whether drawdown was approaching limits.
    # Now we explicitly flag thresholds:
    #   ≥ -3% daily P&L: yellow warning
    #   ≥ -6% daily P&L: orange warning (matches scaler soft-limit)
    #   ≥ -8% daily P&L: red alert (matches scaler hard-stop)
    # Equity-relative since absolute thresholds break across capital tiers.
    if equity > 0 and daily_pl < 0:
        _dl_pct = abs(daily_pl) / equity * 100
        if _dl_pct >= 8.0:
            st.error(
                f"🚨 **HARD-STOP THRESHOLD REACHED** — daily drawdown "
                f"-{_dl_pct:.2f}% (≥ 8%). Bot's SCALER should have "
                f"halted entries. Verify in log."
            )
        elif _dl_pct >= 6.0:
            st.warning(
                f"⚠️ **SCALER soft-limit zone** — daily drawdown "
                f"-{_dl_pct:.2f}% (≥ 6%). Entry sizes reduced to 50%."
            )
        elif _dl_pct >= 3.0:
            st.info(
                f"📉 Drawdown caution: daily P&L -{_dl_pct:.2f}% "
                f"(approaching 6% soft-limit)."
            )

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
                # v7.36: support fractional crypto qty (was int(float(p.qty))
                # which truncated 0.001 BTC to 0 and broke cost math).
                raw_qty = float(p.qty)
                entry   = float(p.avg_entry_price)
                current = float(p.current_price)
                # Detect asset class for display + notional math
                _asset_class = str(getattr(p, "asset_class", "") or "").lower()
                _is_option = (_asset_class == "us_option")
                _is_crypto = (_asset_class == "crypto") or ("/" in str(p.symbol))
                # Correct notional by instrument
                if _is_option:
                    cost = abs(raw_qty) * entry * 100.0   # options ×100 multiplier
                else:
                    cost = abs(raw_qty) * entry
                unreal  = float(p.unrealized_pl)
                unr_pct = float(p.unrealized_plpc) * 100
                total_cost += cost
                # Qty display: fractional for crypto, integer for shares/options
                if _is_crypto:
                    qty_display = f"{raw_qty:.6f}".rstrip("0").rstrip(".")
                    tag = " 🪙"
                elif _is_option:
                    qty_display = str(int(raw_qty))
                    tag = " 📜"
                else:
                    qty_display = str(int(raw_qty))
                    tag = ""
                rows.append({
                    "Symbol":   p.symbol + tag,
                    "Qty":      qty_display,
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
# v7.36 fix #3: fall back to session-CSV-derived equity curve if Alpaca's
# portfolio_history returns nothing (common in paper accounts after hours
# or before any trades exist for the day). Pre-v7.36 the chart silently
# went blank; now we try Alpaca first, then approximate from session CSV.
_eq_chart_rendered = False
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
            _eq_chart_rendered = True
except Exception as e:
    st.caption(f"Alpaca portfolio history unavailable ({e}); trying CSV fallback.")

if not _eq_chart_rendered:
    # v7.36: Approximate equity curve from session CSV — start at
    # session_start_equity, apply each trade's net_pnl (or pnl if net
    # not available) cumulatively at closed_time. Not exact since we
    # don't have intra-trade marks, but better than blank chart.
    try:
        df_t = _fetch_session_trades()
        if not df_t.empty and "closed_time" in df_t.columns:
            _pnl_col = "net_pnl" if "net_pnl" in df_t.columns else "pnl"
            df_eq2 = df_t.copy()
            df_eq2["closed_time"] = pd.to_datetime(df_eq2["closed_time"])
            df_eq2 = df_eq2.sort_values("closed_time")
            # Use Alpaca-current equity minus today's net change as start
            try:
                _last_eq = float(client.get_account().last_equity)
            except Exception:
                _last_eq = float(getattr(client.get_account(), "equity", 0))
            df_eq2["cumulative_pnl"] = df_eq2[_pnl_col].cumsum()
            df_eq2["Equity"] = _last_eq + df_eq2["cumulative_pnl"]
            df_eq2 = df_eq2[["closed_time", "Equity"]].rename(
                columns={"closed_time": "Time"}
            )
            if not df_eq2.empty:
                st.caption(
                    "📊 Showing approximated curve from session CSV "
                    f"(using `{_pnl_col}`, marked at trade close times)."
                )
                st.line_chart(df_eq2.set_index("Time")["Equity"])
                _eq_chart_rendered = True
    except Exception as e:
        st.caption(f"CSV fallback also failed: {e}")

if not _eq_chart_rendered:
    st.caption("No equity data available — Alpaca history empty and no closed trades yet today.")

st.divider()

# ── Session trade log ─────────────────────────────────────────────────────────
st.subheader("Today's Session Trades")
try:
    df_trades = _fetch_session_trades()
    if df_trades.empty:
        st.info("No trade log pushed yet. Run tools/run_push_results.bat after session.")
    else:
        # Summary metrics — v7.36: net P&L after fees when columns present
        has_pnl     = "pnl" in df_trades.columns
        has_net_pnl = "net_pnl" in df_trades.columns
        has_fees    = "fees_total_usd" in df_trades.columns
        has_strat   = "strategy" in df_trades.columns

        total_t   = len(df_trades)
        wins      = int((df_trades["pnl"] > 0).sum()) if has_pnl else 0
        total_pnl = float(df_trades["pnl"].sum())     if has_pnl else 0.0
        wr        = wins / total_t * 100 if total_t > 0 else 0
        total_fees = float(df_trades["fees_total_usd"].sum()) if has_fees else 0.0
        total_net  = float(df_trades["net_pnl"].sum()) if has_net_pnl else total_pnl

        # Show 5 metrics when v7.36 columns present, 4 otherwise
        if has_net_pnl and has_fees:
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Trades",   total_t)
            m2.metric("Wins",     wins)
            m3.metric("Win Rate", f"{wr:.1f}%")
            m4.metric("Gross P&L", f"${total_pnl:+,.2f}")
            m5.metric(
                "Net P&L (after fees)",
                f"${total_net:+,.2f}",
                delta=f"-${total_fees:.2f} fees" if total_fees > 0 else None,
                delta_color="inverse" if total_fees > 0 else "off",
            )
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Trades",   total_t)
            m2.metric("Wins",     wins)
            m3.metric("Win Rate", f"{wr:.1f}%")
            m4.metric("P&L",      f"${total_pnl:+,.2f}")

        # v7.36: strategy breakdown (scalp vs hybrid vs shares/options)
        if has_strat and has_pnl:
            try:
                by_strat = df_trades.groupby("strategy").agg(
                    trades=("pnl", "count"),
                    gross=("pnl", "sum"),
                ).reset_index()
                if not by_strat.empty and len(by_strat) > 1:
                    st.caption("Strategy breakdown:")
                    _rows = []
                    for _, row in by_strat.iterrows():
                        _name = str(row["strategy"]) or "(unspecified)"
                        _rows.append(
                            f"**{_name}**: {int(row['trades'])} trades, "
                            f"gross ${float(row['gross']):+,.2f}"
                        )
                    st.markdown("  ·  ".join(_rows))
            except Exception:
                pass

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

        # Display table — v7.0 + v7.36 columns
        disp_cols = [c for c in [
            "symbol", "direction", "entry_time", "entry_price",
            "exit_price", "exit_reason", "pnl", "pnl_pct",
            "strategy", "entry_order_type",        # v7.36
            "fees_total_usd", "net_pnl", "net_pnl_pct",  # v7.36
            "score", "tfs_agreed"
        ] if c in df_trades.columns]

        if has_pnl:
            def _color_pnl(val):
                if isinstance(val, str):
                    return "color: green" if (val.startswith("$+") or val.startswith("+")) else "color: red" if "-" in val else ""
                if not isinstance(val, (int, float)):
                    return ""
                return "color: green" if val >= 0 else "color: red"
            pnl_cols = [c for c in [
                "pnl", "pnl_pct", "net_pnl", "net_pnl_pct"
            ] if c in df_trades.columns]
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

# ═══════════════════════════════════════════════════════════════════════════
#  v7.36 fix #6 — Dedicated CRYPTO panel (regime, scalper status, recent BTC)
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("🪙 Crypto (BTC/USD)")
_ccol1, _ccol2, _ccol3 = st.columns(3)

# Regime status
_regime = _read_btc_regime()
with _ccol1:
    st.markdown("**Current BTC Regime**")
    if _regime == "unknown":
        st.caption("Regime file not accessible (remote dashboard)")
    else:
        # btc_regime.txt is "RANGING | 14:32 ET" format
        st.markdown(f"`{_regime}`")

# Scalper status (best-effort: read from latest bot init log line)
with _ccol2:
    st.markdown("**Scalper Status**")
    _scalper_status = "unknown"
    if _health.get("available"):
        try:
            import os, re
            here = os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(here, "..", "trading_bot.log")
            if os.path.exists(log_path):
                with open(log_path, "rb") as f:
                    try:
                        f.seek(-50_000, os.SEEK_END)
                    except OSError:
                        f.seek(0)
                    tail = f.read().decode("utf-8", errors="ignore")
                if "v7.36 crypto scalper ENABLED" in tail:
                    _scalper_status = "✅ ENABLED"
                elif "v7.36 crypto scalper DISABLED" in tail:
                    _scalper_status = "⏸️ DISABLED"
        except Exception:
            pass
    st.markdown(f"`{_scalper_status}`")

# Today's BTC trades summary
with _ccol3:
    st.markdown("**Today's BTC Trades**")
    try:
        df_today = _fetch_session_trades()
        if not df_today.empty and "symbol" in df_today.columns:
            df_btc = df_today[df_today["symbol"].str.contains(
                "BTC", na=False, regex=False
            )]
            if df_btc.empty:
                st.caption("No BTC trades today.")
            else:
                _scalp_count = 0
                _hybrid_count = 0
                if "strategy" in df_btc.columns:
                    _scalp_count  = int((df_btc["strategy"] == "scalp").sum())
                    _hybrid_count = int((df_btc["strategy"] == "hybrid").sum())
                _btc_pnl_col = "net_pnl" if "net_pnl" in df_btc.columns else "pnl"
                _btc_pnl = float(df_btc[_btc_pnl_col].sum())
                st.markdown(
                    f"Trades: {len(df_btc)} "
                    f"(scalp: {_scalp_count}, hybrid: {_hybrid_count})  \n"
                    f"P&L: ${_btc_pnl:+,.2f}"
                )
        else:
            st.caption("No trade data for today.")
    except Exception:
        st.caption("BTC summary unavailable.")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
#  v7.36 fix #4 — Multi-day comparison view
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("📅 Last 7 Days")
try:
    df_recent = _fetch_recent_session_trades(days=7)
    if df_recent.empty:
        st.info("No multi-day session data yet (need at least one closed session).")
    else:
        # Per-day summary
        if "session_date" in df_recent.columns and "pnl" in df_recent.columns:
            _net_col = "net_pnl" if "net_pnl" in df_recent.columns else "pnl"
            agg = df_recent.groupby("session_date").agg(
                trades=("pnl", "count"),
                wins=("pnl", lambda s: int((s > 0).sum())),
                gross_pnl=("pnl", "sum"),
                net_pnl=(_net_col, "sum"),
            ).reset_index()
            agg["win_rate"] = (
                agg["wins"] / agg["trades"] * 100
            ).round(1).astype(str) + "%"
            agg["gross_pnl"] = agg["gross_pnl"].apply(lambda v: f"${v:+,.2f}")
            agg["net_pnl"]   = agg["net_pnl"].apply(lambda v: f"${v:+,.2f}")
            # Pretty date display
            try:
                agg["date"] = pd.to_datetime(
                    agg["session_date"], format="%Y%m%d"
                ).dt.strftime("%a %m/%d")
            except Exception:
                agg["date"] = agg["session_date"]
            agg = agg[["date", "trades", "wins", "win_rate",
                       "gross_pnl", "net_pnl"]]
            st.dataframe(agg, use_container_width=True, hide_index=True)

            # Cumulative net P&L line chart
            try:
                df_chart = df_recent.copy()
                df_chart["session_date"] = pd.to_datetime(
                    df_chart["session_date"], format="%Y%m%d"
                )
                _net_col2 = "net_pnl" if "net_pnl" in df_chart.columns else "pnl"
                daily_pnl = df_chart.groupby("session_date")[_net_col2].sum()
                cum_pnl = daily_pnl.cumsum()
                if not cum_pnl.empty:
                    st.caption("📈 Cumulative net P&L over the last 7 days:")
                    st.line_chart(cum_pnl)
            except Exception:
                pass

            # Strategy breakdown across the week
            if "strategy" in df_recent.columns:
                try:
                    by_strat = df_recent.groupby("strategy").agg(
                        trades=("pnl", "count"),
                        net_pnl=(_net_col, "sum"),
                    ).reset_index()
                    if len(by_strat) > 1:
                        st.caption("**Weekly strategy breakdown:**")
                        _bits = []
                        for _, row in by_strat.iterrows():
                            _name = str(row["strategy"]) or "(unspecified)"
                            _bits.append(
                                f"**{_name}**: {int(row['trades'])} trades, "
                                f"net ${float(row['net_pnl']):+,.2f}"
                            )
                        st.markdown("  ·  ".join(_bits))
                except Exception:
                    pass
        else:
            st.dataframe(df_recent.tail(30), use_container_width=True, hide_index=True)
except Exception as e:
    st.caption(f"Multi-day view error: {e}")

st.divider()
st.caption(
    f"Trading Bot v7.36 | {'Paper' if is_paper else 'LIVE'} | "
    f"speedracer1186 | {datetime.now().strftime('%Y-%m-%d')}"
)
