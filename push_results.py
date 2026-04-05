"""
push_results.py — Auto-push today's session trade log to GitHub (v6.7.0)
=========================================================================
Finds today's session_paper_YYYYMMDD.csv (or session_live_*.csv) in the
results/ folder, stages it in git, commits, and pushes to the private
GitHub repo so the Streamlit dashboard can display it.

Called automatically by the bot at session end (via main.py _end_of_session).
Can also be run manually at any time:

    python push_results.py              # push today's results
    python push_results.py --all        # push all CSVs in results/
    python push_results.py --dry-run    # show what would be pushed, no git

Requirements:
    - git must be installed (git-scm.com)
    - git remote 'origin' must be configured pointing to your GitHub repo
    - Run from C:\\Users\\wasee\\TradingBot\\
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _run(cmd: list, cwd: Path, dry_run: bool = False) -> tuple:
    if dry_run:
        print(f"  [dry-run] {chr(32).join(cmd)}")
        return 0, ""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd),
            capture_output=True, text=True, timeout=30
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "git command timed out"
    except FileNotFoundError:
        return 1, "git not found — install from git-scm.com"
    except Exception as e:
        return 1, str(e)


def _find_today(results_dir: Path) -> list:
    today = datetime.now().strftime("%Y%m%d")
    found = []
    for pat in [f"session_paper_{today}.csv", f"session_live_{today}.csv",
                f"summary_paper_{today}_*.csv", f"summary_live_{today}_*.csv"]:
        found.extend(results_dir.glob(pat))
    return sorted(set(found))


def _find_all(results_dir: Path) -> list:
    found = []
    for pat in ["session_*.csv", "summary_*.csv"]:
        found.extend(results_dir.glob(pat))
    return sorted(set(found))


def push_results(files: list, repo_dir: Path,
                 dry_run: bool = False, verbose: bool = True) -> bool:
    if not files:
        if verbose: print("  No result files to push.")
        return True

    if verbose:
        print(f"\n  Files to push ({len(files)}):")
        for f in files:
            print(f"    {f.name}  ({f.stat().st_size:,} bytes)")

    # Verify git repo
    rc, out = _run(["git", "status", "--short"], repo_dir, dry_run=False)
    if rc != 0 and not dry_run:
        print(f"  ERROR: {out}")
        print(f"  Run: cd {repo_dir} && git init && "
              f"git remote add origin https://github.com/USER/REPO.git")
        return False

    # Pull latest first
    if verbose: print("  Pulling latest…")
    _run(["git", "pull", "--rebase", "origin", "main"], repo_dir, dry_run)

    # Stage
    if verbose: print("  Staging…")
    rel = [str(f.relative_to(repo_dir)) for f in files]
    rc, out = _run(["git", "add"] + rel, repo_dir, dry_run)
    if rc != 0:
        print(f"  ERROR staging: {out}"); return False

    # Check diff
    rc2, diff = _run(["git", "diff", "--cached", "--name-only"], repo_dir, dry_run)
    if not dry_run and not diff.strip():
        if verbose: print("  Nothing new — files already up to date on GitHub.")
        return True

    # Commit
    msg = f"bot: session results {datetime.now().strftime('%Y%m%d %H:%M')}"
    rc, out = _run(["git", "commit", "-m", msg], repo_dir, dry_run)
    if rc != 0 and not dry_run:
        print(f"  ERROR committing: {out}"); return False
    if verbose: print(f"  Committed: {msg}")

    # Push
    if verbose: print("  Pushing…")
    rc, out = _run(["git", "push", "origin", "main"], repo_dir, dry_run)
    if rc != 0 and not dry_run:
        print(f"  ERROR pushing: {out}")
        print("  Check your Personal Access Token.")
        return False

    if verbose: print("  Pushed — dashboard updates in ~60 seconds.")
    return True


def auto_push_today(repo_dir=None, verbose: bool = True) -> bool:
    """Called from main.py _end_of_session(). Never raises."""
    try:
        root = (Path(repo_dir) if repo_dir
                else Path(__file__).parent)
        results_dir = root / "results"
        files = _find_today(results_dir)
        if not files:
            return True
        return push_results(files, root, dry_run=False, verbose=verbose)
    except Exception as e:
        if verbose: print(f"  Auto-push failed (non-fatal): {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Push session results to GitHub")
    parser.add_argument("--all",     action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet",   action="store_true")
    args = parser.parse_args()

    root        = Path(__file__).parent
    results_dir = root / "results"
    verbose     = not args.quiet

    print(); print("=" * 55)
    print("  TRADING BOT — Push Results to GitHub")
    print(f"  {'DRY RUN — ' if args.dry_run else ''}Repo: {root}")
    print("=" * 55)

    if not results_dir.exists():
        print("  No results/ folder. Run paper trading first.")
        sys.exit(0)

    files = _find_all(results_dir) if args.all else _find_today(results_dir)
    if not files:
        print(f"  No {'all' if args.all else 'today'} files in {results_dir}")
        sys.exit(0)

    ok = push_results(files, root, dry_run=args.dry_run, verbose=verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
