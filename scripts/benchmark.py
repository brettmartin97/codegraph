#!/usr/bin/env python3
"""Benchmark CodeGraph indexing speed on real open-source repos.

Usage:
    python scripts/benchmark.py                    # quick: requests only
    python scripts/benchmark.py --full             # all repos
    python scripts/benchmark.py --repo flask       # specific repo
"""
from __future__ import annotations
import argparse, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

REPOS = {
    "requests": ("https://github.com/psf/requests", "main"),
    "flask":    ("https://github.com/pallets/flask", "main"),
    "gin":      ("https://github.com/gin-gonic/gin", "master"),
    "fastapi":  ("https://github.com/tiangolo/fastapi", "master"),
}

def clone(url: str, branch: str, dest: Path) -> float:
    t0 = time.perf_counter()
    subprocess.run(
        ["git", "clone", "--depth=1", "--branch", branch, url, str(dest)],
        capture_output=True, check=True,
    )
    return time.perf_counter() - t0

def run_bench(name: str, repo_path: Path) -> dict:
    from codegraph_mcp.graph.sqlite_store import SQLiteStore
    from codegraph_mcp.indexing.indexer import Indexer
    from codegraph_mcp.config import settings
    settings.allow_external_repos = True

    db_path = repo_path.parent / f"{name}.db"
    store = SQLiteStore(db_path)
    indexer = Indexer(store)

    t0 = time.perf_counter()
    indexer.add_repo(name, repo_path)
    result = indexer.index_repo(name, "full")
    elapsed = time.perf_counter() - t0

    repo = store.get_repo(name)
    ov = store.overview(repo.id) if repo else {}

    # Incremental benchmark (no changes — should be near-instant)
    t1 = time.perf_counter()
    indexer.index_repo(name, "incremental")
    incr_elapsed = time.perf_counter() - t1

    return {
        "repo": name,
        "files": result["files_seen"],
        "functions": result["functions_seen"],
        "edges": result["edges_seen"],
        "full_index_s": round(elapsed, 2),
        "incremental_s": round(incr_elapsed, 3),
        "fns_per_sec": round(result["functions_seen"] / elapsed) if elapsed > 0 else 0,
        "languages": ov.get("languages", {}),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--repo", help="Benchmark a specific repo name")
    args = parser.parse_args()

    to_bench = [args.repo] if args.repo else (list(REPOS) if args.full else ["requests"])
    missing = [r for r in to_bench if r not in REPOS]
    if missing:
        print(f"Unknown repos: {missing}. Available: {list(REPOS)}", file=sys.stderr)
        sys.exit(1)

    print(f"{'Repo':<15} {'Files':>6} {'Functions':>10} {'Edges':>8} {'Full (s)':>10} {'Incremental (s)':>17} {'fn/s':>8}")
    print("-" * 80)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for name in to_bench:
            url, branch = REPOS[name]
            dest = Path(tmp) / name
            print(f"Cloning {name}...", end=" ", flush=True)
            try:
                clone(url, branch, dest)
            except subprocess.CalledProcessError as e:
                print(f"CLONE FAILED: {e}")
                continue
            result = run_bench(name, dest)
            print(
                f"\r{result['repo']:<15} {result['files']:>6} {result['functions']:>10} "
                f"{result['edges']:>8} {result['full_index_s']:>10} "
                f"{result['incremental_s']:>17} {result['fns_per_sec']:>8}"
            )

if __name__ == "__main__":
    main()
