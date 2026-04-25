"""
push_results.py — Push session trade logs to GitHub via REST API (v7.0)
========================================================================
Uses the GitHub REST API directly — no git installation or repo setup needed.
Just needs GITHUB_TOKEN and GITHUB_REPO set in config.py or passed as args.

Called automatically by main.py at end of session when AUTO_PUSH_RESULTS=True.
Can also be run manually from C:\\Users\\wasee\\TradingBot\\:
    python tools\\push_results.py              # push today's session CSV
    python tools\\push_results.py --all        # push all CSVs in results\\
    python tools\\push_results.py --dry-run    # show what would be pushed

The Streamlit dashboard reads these files from GitHub and displays them.
Files are pushed to the root of your GITHUB_REPO.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load GITHUB_TOKEN and GITHUB_REPO from config.py."""
    try:
        src = Path(__file__).parent.parent / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        import config
        return {
            "token": getattr(config, "GITHUB_TOKEN",      ""),
            "repo":  getattr(config, "GITHUB_REPO",       ""),
            "auto":  getattr(config, "AUTO_PUSH_RESULTS", False),
        }
    except Exception as e:
        return {"token": "", "repo": "", "auto": False, "_err": str(e)}


# ── GitHub API helpers ─────────────────────────────────────────────────────────

def _api_request(method: str, url: str, token: str,
                  data: dict | None = None) -> tuple[int, dict]:
    """Make a GitHub API request. Returns (status_code, response_dict)."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github.v3+json",
        "Content-Type":  "application/json",
        "User-Agent":    "TradingBot-v7.0",
    }
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {"message": str(e)}
        return e.code, body
    except Exception as e:
        return 0, {"message": str(e)}


def _get_file_sha(repo: str, token: str, filename: str) -> str | None:
    """Get the SHA of an existing file (needed for update vs create)."""
    url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    status, resp = _api_request("GET", url, token)
    if status == 200:
        return resp.get("sha")
    return None


def _push_file(repo: str, token: str, filename: str,
               content: bytes, dry_run: bool = False) -> bool:
    """Create or update a file in the GitHub repo via REST API."""
    if dry_run:
        print(f"  [dry-run] Would push: {filename} ({len(content):,} bytes)")
        return True

    url      = f"https://api.github.com/repos/{repo}/contents/{filename}"
    sha      = _get_file_sha(repo, token, filename)
    b64      = base64.b64encode(content).decode()
    msg      = f"bot: session results {datetime.now().strftime('%Y%m%d %H:%M')}"

    payload: dict = {"message": msg, "content": b64}
    if sha:
        payload["sha"] = sha   # required for update

    status, resp = _api_request("PUT", url, token, payload)

    if status in (200, 201):
        action = "Updated" if sha else "Created"
        print(f"  {action}: {filename}")
        return True
    else:
        print(f"  ERROR pushing {filename}: {resp.get('message', 'unknown error')}")
        return False


# ── File finders ──────────────────────────────────────────────────────────────

def _find_today(results_dir: Path) -> list[Path]:
    today = datetime.now().strftime("%Y%m%d")
    found = []
    for pat in [f"session_paper_{today}*.csv", f"session_live_{today}*.csv",
                f"summary_paper_{today}*.csv",  f"summary_live_{today}*.csv"]:
        found.extend(results_dir.glob(pat))
    # v7.0: also include btc_regime.txt from data/ folder
    regime_file = results_dir.parent / "data" / "btc_regime.txt"
    if regime_file.exists():
        found.append(regime_file)
    return sorted(set(found))


def _find_all(results_dir: Path) -> list[Path]:
    found = []
    for pat in ["session_*.csv", "summary_*.csv"]:
        found.extend(results_dir.glob(pat))
    return sorted(set(found))


# ── Main push logic ───────────────────────────────────────────────────────────

def push_results(files: list[Path], token: str, repo: str,
                 dry_run: bool = False, verbose: bool = True) -> bool:
    """Push a list of result files to GitHub. Returns True on full success."""
    if not files:
        if verbose:
            print("  No result files to push.")
        return True

    if not token or token.startswith("YOUR_"):
        print("  ERROR: GITHUB_TOKEN not set in config.py")
        print("  Set GITHUB_TOKEN = 'ghp_...' in src/config.py to enable auto-push.")
        return False

    if not repo or "/" not in repo:
        print(f"  ERROR: GITHUB_REPO not set correctly (got: '{repo}')")
        print("  Set GITHUB_REPO = 'yourusername/Trading-Bot-Dashboard' in config.py")
        return False

    if verbose:
        print(f"\n  Pushing {len(files)} file(s) to github.com/{repo}")

    ok = True
    for f in files:
        try:
            content = f.read_bytes()
            result  = _push_file(repo, token, f.name, content, dry_run)
            if not result:
                ok = False
        except Exception as e:
            print(f"  ERROR reading {f.name}: {e}")
            ok = False

    if ok and verbose and not dry_run:
        print("  Done — dashboard updates in ~30 seconds.")

    return ok


def auto_push_today(repo_dir=None, verbose: bool = True) -> bool:
    """
    Called from main.py _end_of_session(). Never raises.
    Also called at session start to push any incomplete files from prior runs.
    """
    try:
        cfg         = _load_config()
        root        = Path(repo_dir) if repo_dir else Path(__file__).parent.parent
        results_dir = root / "results"
        files       = _find_today(results_dir)
        if not files:
            return True
        return push_results(
            files,
            token    = cfg["token"],
            repo     = cfg["repo"],
            dry_run  = False,
            verbose  = verbose,
        )
    except Exception as e:
        if verbose:
            print(f"  Auto-push failed (non-fatal): {e}")
        return False


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Push session CSVs to GitHub dashboard repo via REST API"
    )
    parser.add_argument("--all",     action="store_true",
                        help="Push all session CSVs, not just today's")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be pushed without actually pushing")
    parser.add_argument("--quiet",   action="store_true")
    args = parser.parse_args()

    cfg         = _load_config()
    root        = Path(__file__).parent.parent
    results_dir = root / "results"
    verbose     = not args.quiet

    print(); print("=" * 55)
    print("  TRADING BOT v7.36.3 — Push Results to GitHub")
    print(f"  {'DRY RUN — ' if args.dry_run else ''}Repo: {cfg.get('repo','NOT SET')}")
    print("=" * 55)

    if "_err" in cfg:
        print(f"  WARNING: Could not load config.py: {cfg['_err']}")

    if not results_dir.exists():
        print("  No results/ folder found. Run paper trading first.")
        sys.exit(0)

    files = _find_all(results_dir) if args.all else _find_today(results_dir)

    if not files:
        label = "all" if args.all else "today's"
        print(f"  No {label} session files found in {results_dir}")
        sys.exit(0)

    if verbose:
        print(f"\n  Files to push ({len(files)}):")
        for f in files:
            print(f"    {f.name}  ({f.stat().st_size:,} bytes)")

    ok = push_results(
        files,
        token    = cfg["token"],
        repo     = cfg["repo"],
        dry_run  = args.dry_run,
        verbose  = verbose,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
