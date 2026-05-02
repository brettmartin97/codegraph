from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich import print

from codegraph_mcp.config import ensure_runtime_dirs, settings
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.memory.failures import FailureIngestor
from codegraph_mcp.query.context import ContextEngine
from codegraph_mcp.query.impact import ImpactEngine

app = typer.Typer(help="CodeGraph MCP CLI")
repo_app = typer.Typer(help="Repository management")
ci_app  = typer.Typer(help="CI/CD pipeline helpers")
app.add_typer(repo_app, name="repo")
app.add_typer(ci_app, name="ci")

ensure_runtime_dirs()
store = SQLiteStore(settings.db_path)
indexer = Indexer(store)
context = ContextEngine(store)
impact_engine = ImpactEngine(store)
failure_ingestor = FailureIngestor(store)


def emit(data, as_json: bool):
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        print(data)


# ── repo sub-commands ──────────────────────────────────────────────────────────

@repo_app.command("add")
def repo_add(name: str, path: Path):
    typer.echo(json.dumps(indexer.add_repo(name, path).model_dump(), default=str))


@repo_app.command("add-zip")
def repo_add_zip(
    name: str,
    zip_path: Path = typer.Argument(..., help="Path to the .zip archive"),
    index: bool = typer.Option(True, "--index/--no-index", help="Index the repo immediately after extraction"),
    mode: str = typer.Option("full", help="Indexing mode: full or incremental"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Register a repository from a zip archive and optionally index it."""
    repo = indexer.add_repo_from_zip(name, zip_path)
    if index:
        result = indexer.index_repo(repo.name, mode)
        emit(result, json_out)
    else:
        emit(repo.model_dump(), json_out)


@repo_app.command("list")
def repo_list(json_out: bool = typer.Option(False, "--json")):
    emit([r.model_dump() for r in store.list_repos()], json_out)


# ── top-level commands ────────────────────────────────────────────────────────

@app.command()
def index(repo: str, mode: str = "full", json_out: bool = typer.Option(False, "--json")):
    result = indexer.index_repo(repo, mode)
    # Auto-enrich (heuristic, zero-cost) when enabled
    try:
        from codegraph_mcp.config import settings
        if settings.enrichment_auto:
            from codegraph_mcp.enrichment.runner import make_enricher, run_enrichment
            r = store.get_repo(repo)
            if r:
                enricher = make_enricher(settings.enrichment_backend)
                stats = run_enrichment(
                    store, r.id, enricher,
                    batch_size=settings.enrichment_batch_size,
                    max_functions=settings.enrichment_max_functions,
                )
                enricher.close()
                result["enrichment"] = stats
    except Exception as exc:
        result["enrichment_error"] = str(exc)
    emit(result, json_out)


@app.command()
def overview(repo: str, json_out: bool = typer.Option(False, "--json")):
    emit(context.repo_overview(repo), json_out)


@app.command("find-function")
def find_function(repo: str, query: str, limit: int = 10, json_out: bool = typer.Option(False, "--json")):
    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")
    emit([f.model_dump() for f in store.find_functions(r.id, query, limit)], json_out)


@app.command()
def impact(
    repo: str,
    function: str = typer.Option(..., "--function"),
    depth: int = 2,
    max_tokens: int = 12000,
    json_out: bool = typer.Option(False, "--json"),
):
    emit(impact_engine.function_impact(repo, function, depth, max_tokens).model_dump(), json_out)


@app.command("prepare-change")
def prepare_change(repo: str, task: str, max_tokens: int = 12000, json_out: bool = typer.Option(False, "--json")):
    emit(context.prepare_change(repo, task, max_tokens), json_out)


@app.command()
def snapshot(
    repo: str,
    ref: str = typer.Option("HEAD", "--ref"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Create a named snapshot of the current function graph for this repo."""
    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")
    count = store.snapshot_repo(r.id, ref)
    emit({"repo": repo, "ref": ref, "functions_snapshotted": count}, json_out)


@app.command()
def diff(
    repo: str,
    base: str = typer.Option("HEAD", "--base"),
    head: str = typer.Option("latest", "--head"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Compare two function graph snapshots (base vs head) for a repo."""
    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")
    emit(store.diff_snapshots(r.id, base, head).model_dump(), json_out)


@app.command("validate-boundaries")
def validate_boundaries(
    repo: str,
    policy: Path = typer.Option(Path("boundaries.yaml"), "--policy"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Check CALLS edges in *repo* against a boundary policy YAML file."""
    from codegraph_mcp.policy.boundaries import BoundaryChecker, load_policy
    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")
    policy_path = policy if policy.is_absolute() else Path(r.path) / policy
    try:
        bpolicy = load_policy(policy_path)
    except FileNotFoundError:
        typer.echo(f"Policy file not found: {policy_path}", err=True)
        raise typer.Exit(1)
    checker = BoundaryChecker(store)
    violations = checker.check(r.id, bpolicy)
    emit({"repo": repo, "violations": [v.model_dump() for v in violations]}, json_out)
    if violations and not json_out:
        typer.echo(f"[bold red]{len(violations)} boundary violation(s) found.[/]", err=True)
        raise typer.Exit(1)


@app.command("ingest-failure")
def ingest_failure(
    repo: str,
    kind: str = typer.Option("test_failure", "--kind"),
    message: str = typer.Option("", "--message"),
    file: Path | None = typer.Option(None, "--file", help="Path to a file containing the stack trace"),
    source: str = typer.Option("cli", "--source"),
    language: str = typer.Option("python", "--language"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Ingest a failure event (stack trace) into the graph store for a repo."""
    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")
    if file:
        stack_trace = file.read_text(encoding="utf-8", errors="replace")
    else:
        # Allow piped stdin
        if not sys.stdin.isatty():
            stack_trace = sys.stdin.read()
        else:
            stack_trace = ""
    event = failure_ingestor.ingest(r.id, kind=kind, message=message, stack_trace=stack_trace, source=source, language=language)
    emit({"id": event.id, "kind": event.kind, "functions_matched": event.function_ids}, json_out)


@app.command()
def doctor():
    """Health check: verify config, DB, analyzers, and optional deps."""
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: str = ""):
        status = "ok" if ok else "fail"
        checks.append({"check": name, "status": status, "detail": detail})
        icon = "[green]OK[/]" if ok else "[red]FAIL[/]"
        msg = f"{icon} {name}"
        if detail:
            msg += f"  - {detail}"
        print(msg)

    print("[bold]CodeGraph MCP - Doctor[/bold]\n")

    # DB
    try:
        settings.db_path.exists() or not settings.db_path.exists()
        repos = store.list_repos()
        chk("database", True, f"{settings.db_path} ({len(repos)} repo(s))")
    except Exception as exc:
        chk("database", False, str(exc))

    # Analyzers
    from codegraph_mcp.analyzers.registry import AnalyzerRegistry
    from codegraph_mcp.analyzers.treesitter import TREE_SITTER_AVAILABLE
    reg = AnalyzerRegistry()
    chk("tree-sitter analyzers", TREE_SITTER_AVAILABLE,
        f"languages: {', '.join(reg.active_languages)}" if TREE_SITTER_AVAILABLE
        else "install: pip install 'codegraph-mcp[treesitter]'")

    # Watch
    try:
        import watchfiles
        chk("watch mode", True, f"watchfiles {watchfiles.__version__}")
    except ImportError:
        chk("watch mode", False, "install: pip install 'codegraph-mcp[watch]'")

    # MCP server
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401  (availability probe)
        chk("mcp server", True, "ready")
    except ImportError:
        chk("mcp server", False, "install: pip install 'codegraph-mcp[mcp]'")

    # Paths
    chk("repo_root exists", settings.repo_root.exists(), str(settings.repo_root))
    chk("db dir writable", settings.db_path.parent.exists() or True,
        str(settings.db_path.parent))

    print()
    fails = [c for c in checks if c["status"] == "fail"]
    if fails:
        print(f"[red]{len(fails)} check(s) failed.[/red]")
        raise typer.Exit(1)
    else:
        print("[green]All checks passed.[/green]")

@app.command()
def init(
    path: Path = typer.Argument(
        None, help="Repo path to register (defaults to current directory)"
    ),
    name: str = typer.Option("", "--name", help="Repo name (defaults to directory name)"),
    watch_after: bool = typer.Option(False, "--watch", help="Start watch mode after indexing"),
    json_out: bool = typer.Option(False, "--json"),
):
    """One-command setup: register, index, and optionally watch a repo.

    Examples:
      codegraph init                     # index current directory
      codegraph init /path/to/repo       # index a specific path
      codegraph init /path/to/repo --watch  # index then watch
    """
    from codegraph_mcp.analyzers.treesitter import TREE_SITTER_AVAILABLE
    target = Path(path).resolve() if path else Path.cwd()
    repo_name = name or target.name

    if not target.exists():
        typer.echo(f"Path does not exist: {target}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Registering {repo_name!r} at {target}")
    repo = indexer.add_repo(repo_name, target)

    if not TREE_SITTER_AVAILABLE:
        typer.echo(
            "[yellow]Tip: install tree-sitter for real AST analysis of JS/TS/Go:[/yellow]\n"
            "  pip install \'codegraph-mcp[treesitter]\'",
        )

    typer.echo("Indexing...")
    result = indexer.index_repo(repo.name, "full")
    typer.echo(
        f"Done - {result['files_seen']} files, "
        f"{result['functions_seen']} functions, "
        f"{result['edges_seen']} edges"
    )
    if result.get("diagnostics"):
        typer.echo(f"  {len(result['diagnostics'])} diagnostic(s) - run with --json to see all")

    if json_out:
        emit(result, True)

    if watch_after:
        typer.echo("Watching for changes...")
        try:
            from watchfiles import watch as wf_watch
            for changes in wf_watch(str(target)):
                for change_type, file_path in changes:
                    fp = Path(file_path)
                    if fp.suffix not in {
                        ".py",".js",".ts",".tsx",".jsx",".go",
                        ".java",".cs",".rs",".rb",".php",".kt",
                    }:
                        continue
                    try:
                        r = indexer.index_file(repo_name, fp)
                        if not r.get("skipped"):
                            typer.echo(
                                f"  [{change_type.name}] {r.get('file', fp.name)}"
                                f" -> {r.get('functions_seen', 0)} fn(s)"
                            )
                    except Exception as exc:
                        typer.echo(f"  Error: {exc}", err=True)
        except ImportError:
            typer.echo(
                "watchfiles not installed. Run: pip install 'codegraph-mcp[watch]'",
                err=True,
            )



# ── status ────────────────────────────────────────────────────────────────────

@app.command()
def status(
    json_out: bool = typer.Option(False, "--json"),
):
    """Show all registered repos with function counts and graph freshness.

    Examples:
      codegraph status
      codegraph status --json
    """
    repos = store.list_repos()
    if not repos:
        typer.echo("No repos indexed yet. Run: codegraph init <path>")
        raise typer.Exit(0)

    rows = []
    for repo in repos:
        freshness = store.graph_freshness(repo.id)
        stale_h = freshness.get("hours_stale")
        if stale_h is None:
            age_str = "never indexed"
        elif stale_h < 1:
            age_str = "< 1 h ago"
        elif stale_h < 24:
            age_str = f"{stale_h:.0f} h ago"
        else:
            age_str = f"{stale_h / 24:.1f} d ago"

        rows.append({
            "name": repo.name,
            "path": str(repo.path),
            "files": freshness.get("file_count", 0),
            "last_indexed": age_str,
            "warning": freshness.get("warning") or "",
        })

    if json_out:
        emit(rows, True)
        return

    typer.echo(f"{'Repo':<20} {'Files':>7}  {'Last indexed':<14}  Path")
    typer.echo("-" * 68)
    for r in rows:
        warn_flag = " !" if r["warning"] else ""
        typer.echo(
            f"{r['name']:<20} {r['files']:>7}  {r['last_indexed']:<14}  {r['path']}{warn_flag}"
        )
    if any(r["warning"] for r in rows):
        typer.echo("\n!  One or more repos have stale graphs - run `codegraph init <name>` to refresh.")


# ── ci sub-commands ───────────────────────────────────────────────────────────

@ci_app.command("index")
def ci_index(
    repo: str,
    path: Path = typer.Option(..., "--path", help="Repo root or zip archive"),
    json_out: bool = typer.Option(False, "--json"),
):
    """CI helper: register and index a repo from a path or zip archive."""
    if str(path).endswith(".zip"):
        repo_obj = indexer.add_repo_from_zip(repo, path)
    else:
        repo_obj = indexer.add_repo(repo, path)
    result = indexer.index_repo(repo_obj.name, "full")
    emit(result, json_out)


@ci_app.command("pr-impact")
def ci_pr_impact(
    repo: str,
    functions: list[str] = typer.Option(..., "--function", help="Functions to assess (repeat for multiple)"),
    depth: int = typer.Option(2, "--depth"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
    fail_on_high: bool = typer.Option(False, "--fail-on-high", help="Exit 1 if any function is high risk"),
):
    """CI helper: assess impact of changed functions and optionally fail on high risk."""
    results = []
    highest_risk = 0.0
    for fn_name in functions:
        try:
            report = impact_engine.function_impact(repo, fn_name, depth, 8000)
            results.append(report.model_dump())
            highest_risk = max(highest_risk, report.risk_score)
        except ValueError as exc:
            results.append({"error": str(exc), "function": fn_name})
    emit(results, json_out)
    if fail_on_high and highest_risk >= 0.65:
        typer.echo(f"Risk score {highest_risk:.2f} >= 0.65 - failing CI", err=True)
        raise typer.Exit(1)


@ci_app.command("validate-boundaries")
def ci_validate_boundaries(
    repo: str,
    policy: Path = typer.Option(Path("boundaries.yaml"), "--policy"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
):
    """CI helper: fail if any boundary violations exist."""
    from codegraph_mcp.policy.boundaries import BoundaryChecker, load_policy
    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")
    policy_path = policy if policy.is_absolute() else Path(r.path) / policy
    try:
        bpolicy = load_policy(policy_path)
    except FileNotFoundError:
        typer.echo(f"Policy file not found: {policy_path}", err=True)
        raise typer.Exit(1)
    checker = BoundaryChecker(store)
    violations = checker.check(r.id, bpolicy)
    emit({"repo": repo, "violations": [v.model_dump() for v in violations]}, json_out)
    if violations:
        typer.echo(f"{len(violations)} boundary violation(s) - failing CI", err=True)
        raise typer.Exit(1)


@ci_app.command("risk-gate")
def ci_risk_gate(
    repo: str,
    functions: list[str] = typer.Option(..., "--function"),
    threshold: float = typer.Option(0.65, "--threshold"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
):
    """CI helper: fail if the maximum risk score across changed functions exceeds threshold."""
    results = []
    highest_risk = 0.0
    for fn_name in functions:
        try:
            report = impact_engine.function_impact(repo, fn_name, 2, 4000)
            results.append({"function": fn_name, "risk_score": report.risk_score, "risk_level": report.risk_level})
            highest_risk = max(highest_risk, report.risk_score)
        except ValueError as exc:
            results.append({"function": fn_name, "error": str(exc)})
    emit({"threshold": threshold, "highest_risk": highest_risk, "passed": highest_risk < threshold, "results": results}, json_out)
    if highest_risk >= threshold:
        typer.echo(f"Risk gate failed: {highest_risk:.2f} >= {threshold}", err=True)
        raise typer.Exit(1)



@ci_app.command("changed-functions")
def ci_changed_functions(
    repo: str,
    diff_file: Path = typer.Option(None, "--diff", help="Unified diff file (default: read stdin)"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
):
    """Parse a unified diff and return graph functions that overlap changed lines.

    Usage in CI:
      git diff origin/main...HEAD | codegraph ci changed-functions myrepo --no-json
      git diff origin/main...HEAD > /tmp/pr.diff && codegraph ci changed-functions myrepo --diff /tmp/pr.diff
    """
    import re

    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")

    # Read diff
    if diff_file:
        raw = Path(diff_file).read_text(encoding="utf-8", errors="ignore")
    else:
        raw = sys.stdin.read()

    # Parse unified diff → {rel_path: set[int]} of changed (added/modified) lines
    changed: dict[str, set[int]] = {}
    current_file: str | None = None
    new_start = 0

    for line in raw.splitlines():
        # +++ b/src/foo.py
        m = re.match(r'^\+\+\+ b/(.+)$', line)
        if m:
            current_file = m.group(1).strip()
            changed.setdefault(current_file, set())
            continue
        # @@ -old_start,old_count +new_start,new_count @@
        m = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
        if m:
            new_start = int(m.group(1))
            continue
        if current_file is None:
            continue
        if line.startswith('+') and not line.startswith('+++'):
            changed[current_file].add(new_start)
            new_start += 1
        elif line.startswith('-') and not line.startswith('---'):
            pass  # deleted lines don't advance new_start
        else:
            new_start += 1

    # Look up functions for each changed file
    results = []
    for rel_path, lines in changed.items():
        if not lines:
            continue
        fns = store.functions_at_lines(r.id, rel_path, lines)
        for fn in fns:
            results.append({
                "file": rel_path,
                "function": fn.name,
                "qualified_name": fn.qualified_name,
                "start_line": fn.start_line,
                "end_line": fn.end_line,
                "is_test": fn.is_test,
            })

    emit(results, json_out)


@ci_app.command("pr-comment")
def ci_pr_comment(
    repo: str,
    base: str = typer.Option("", "--base", help="Git base ref (e.g. main). If set, runs git diff internally."),
    head: str = typer.Option("HEAD", "--head", help="Git head ref (default HEAD)"),
    diff_file: Path = typer.Option(None, "--diff", help="Unified diff file (default: stdin) - ignored if --base is given"),
    depth: int = typer.Option(2, "--depth"),
    threshold: float = typer.Option(0.65, "--threshold", help="Risk score above which functions are flagged"),
    out_file: Path = typer.Option(None, "--out", help="Write markdown to this file (also stdout)"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Generate a markdown PR comment with blast-radius analysis for changed functions.

    Two modes:
      - --base main           : run `git diff base...head` against the repo on disk
      - --diff <file> / stdin : feed a pre-computed unified diff

    Usage in GitHub Actions:
      codegraph ci pr-comment myrepo --base origin/main --out comment.md
    """
    import re

    from codegraph_mcp.query import git_history

    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo {repo}")

    # ── Resolve changed lines ────────────────────────────────────────────────
    if base:
        changed = git_history.changed_line_ranges(Path(r.path), base, head)
    else:
        if diff_file:
            raw = Path(diff_file).read_text(encoding="utf-8", errors="ignore")
        else:
            raw = sys.stdin.read()
        changed = {}
        current_file: str | None = None
        new_start = 0
        for line in raw.splitlines():
            m = re.match(r'^\+\+\+ b/(.+)$', line)
            if m:
                current_file = m.group(1).strip()
                changed.setdefault(current_file, set())
                continue
            m = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
            if m:
                new_start = int(m.group(1))
                continue
            if current_file is None:
                continue
            if line.startswith('+') and not line.startswith('+++'):
                changed[current_file].add(new_start)
                new_start += 1
            elif not line.startswith('-'):
                new_start += 1

    # ── Collect unique functions overlapping changed lines ───────────────────
    seen_ids: set[str] = set()
    fn_entries: list[tuple[str, object]] = []
    for rel_path, lines in changed.items():
        if not lines:
            continue
        fns = store.functions_at_lines(r.id, rel_path, lines)
        for fn in fns:
            if fn.id in seen_ids:
                continue
            seen_ids.add(fn.id)
            fn_entries.append((rel_path, fn))

    if not fn_entries:
        msg = ("## CodeGraph - Call-site Blast Radius\n\n"
               "_No indexed functions overlap the changed lines._\n")
        if out_file:
            Path(out_file).write_text(msg, encoding="utf-8")
        if json_out:
            emit({"comment": msg, "high_risk": [], "functions": []}, True)
        else:
            typer.echo(msg)
        return

    # ── Run impact analysis per function ─────────────────────────────────────
    rows: list[dict] = []
    high_risk: list[dict] = []
    total_callers = 0
    total_tests = 0
    for rel_path, fn in fn_entries:
        try:
            report = impact_engine.function_impact(repo, fn.qualified_name, depth, 4000)
        except ValueError:
            rows.append({
                "qualified_name": fn.qualified_name,
                "file": rel_path,
                "direct_callers": 0,
                "transitive_callers": 0,
                "tests": 0,
                "runtime": 0,
                "risk": 0.0,
                "risk_level": "unknown",
                "reasons": [],
                "validation": [],
            })
            continue
        direct = len(report.direct_callers)
        trans = len(report.transitive_callers)
        tests = len(report.related_tests)
        runtime = len(report.runtime_entrypoints)
        total_callers += direct + trans
        total_tests += tests
        entry = {
            "qualified_name": fn.qualified_name,
            "file": rel_path,
            "start_line": fn.start_line,
            "end_line": fn.end_line,
            "direct_callers": direct,
            "transitive_callers": trans,
            "tests": tests,
            "runtime": runtime,
            "risk": report.risk_score,
            "risk_level": report.risk_level,
            "reasons": report.reasons,
            "validation": report.recommended_validation,
            "callers": [
                {"qualified_name": c.qualified_name,
                 "file": store.file_path(c.file_id), "line": c.start_line}
                for c in report.direct_callers[:5]
            ],
        }
        rows.append(entry)
        if report.risk_score >= threshold:
            high_risk.append(entry)

    rows.sort(key=lambda x: x["risk"], reverse=True)

    # ── Build markdown ───────────────────────────────────────────────────────
    md: list[str] = []
    md.append("## CodeGraph - Call-site Blast Radius")
    md.append("")
    if base:
        md.append(f"_Comparing `{base}` -> `{head}` - {len(rows)} changed function(s) - "
                  f"{total_callers} call-sites reached - {total_tests} related tests_")
    else:
        md.append(f"_{len(rows)} changed function(s) - {total_callers} call-sites reached - "
                  f"{total_tests} related tests_")
    md.append("")
    if high_risk:
        md.append(f"> Warning: **{len(high_risk)} high-risk change(s)** (risk >= {threshold:.2f}) - "
                  "consider extra review and test coverage.")
        md.append("")

    md.append("| Function | File:Line | Callers (direct + transitive) | Tests | Risk |")
    md.append("|---|---|---|---|---|")
    for e in rows:
        risk_marker = "HIGH" if e["risk"] >= threshold else ("MED" if e["risk"] >= 0.35 else "LOW")
        md.append(
            f"| `{e['qualified_name']}` "
            f"| `{e['file']}:{e.get('start_line','?')}` "
            f"| {e['direct_callers']} + {e['transitive_callers']} "
            f"| {e['tests']} "
            f"| {risk_marker} {e['risk']:.2f} ({e['risk_level']}) |"
        )
    md.append("")

    # Detail blocks for high-risk changes
    for e in high_risk:
        md.append(f"<details><summary>HIGH <code>{e['qualified_name']}</code> - risk {e['risk']:.2f}</summary>")
        md.append("")
        md.append(f"**File:** `{e['file']}:{e['start_line']}-{e['end_line']}`")
        md.append("")
        if e["reasons"]:
            md.append("**Why it's risky:**")
            for reason in e["reasons"][:6]:
                md.append(f"- {reason}")
            md.append("")
        if e["callers"]:
            md.append("**Top direct callers:**")
            for c in e["callers"]:
                md.append(f"- `{c['qualified_name']}` - `{c['file']}:{c['line']}`")
            md.append("")
        if e["validation"]:
            md.append("**Recommended validation:**")
            for v in e["validation"][:6]:
                md.append(f"- {v}")
            md.append("")
        md.append("</details>")
        md.append("")

    md.append("---")
    md.append("_Generated by **CodeGraph MCP** - function-first code intelligence._")
    output = "\n".join(md) + "\n"

    if out_file:
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        Path(out_file).write_text(output, encoding="utf-8")

    if json_out:
        emit({
            "comment": output,
            "functions": rows,
            "high_risk_count": len(high_risk),
            "threshold": threshold,
        }, True)
    else:
        typer.echo(output)




@app.command()
def watch(
    repo: str,
    json_out: bool = typer.Option(False, "--json"),
):
    """Watch a repo for file changes and re-index incrementally.

    Requires: pip install codegraph-mcp[watch]

    Keeps the graph live as you edit - no manual re-indexing needed.
    Press Ctrl+C to stop.
    """
    try:
        from watchfiles import watch as wf_watch
    except ImportError:
        typer.echo(
            "watchfiles not installed. Run: pip install 'codegraph-mcp[watch]'",
            err=True,
        )
        raise typer.Exit(1)

    r = store.get_repo(repo)
    if not r:
        raise typer.BadParameter(f"Unknown repo: {repo}")

    repo_path = r.path
    typer.echo(f"Watching {repo_path!r}  (Ctrl+C to stop)")

    _CODE_SUFFIXES = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".go",
        ".java", ".cs", ".rs", ".rb", ".php", ".kt",
    }
    for changes in wf_watch(repo_path):
        for change_type, file_path in changes:
            fp = Path(file_path)
            if fp.suffix not in _CODE_SUFFIXES:
                continue
            try:
                from watchfiles import Change
                if change_type == Change.deleted:
                    result = indexer.remove_file(repo, fp)
                    if not json_out:
                        typer.echo(
                            f"[deleted] {result.get('file', fp.name)}"
                            f" - removed from graph"
                        )
                else:
                    result = indexer.index_file(repo, fp)
                    if not json_out:
                        fns = result.get("functions_seen", 0)
                        if not result.get("skipped"):
                            typer.echo(
                                f"[{change_type.name}] {result.get('file', fp.name)}"
                                f" -> {fns} fn(s) re-indexed"
                            )
                if json_out:
                    import json as _json
                    typer.echo(_json.dumps({**result, "event": change_type.name}))
            except Exception as exc:
                typer.echo(f"Error processing {fp}: {exc}", err=True)
# ── install-mcp ───────────────────────────────────────────────────────────────

@app.command("install-mcp")
def install_mcp(
    python_path: str = typer.Option("", "--python", help="Python executable with codegraph-mcp installed (auto-detected if blank)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change without writing files"),
):
    """Wire CodeGraph MCP into Claude Desktop, Cursor, and/or Zed automatically.

    Detects which AI tools are installed on this machine and appends the
    codegraph-mcp server entry to each config file, creating the file if needed.

    Safe to re-run: if the entry already exists, it is updated in place.

    Examples:
      codegraph install-mcp
      codegraph install-mcp --dry-run
      codegraph install-mcp --python /home/user/.venv/bin/python
    """
    import json as _json
    import os
    import platform
    import shutil
    import sys
    from pathlib import Path as P

    # ── Resolve python executable ─────────────────────────────────────────────
    # Prefer sys.executable: it is guaranteed to be the interpreter that has
    # codegraph_mcp installed (since this CLI is running from it). Falling back
    # to PATH lookups (python3/python) is unreliable on Windows where the Store
    # stub at WindowsApps\python.exe is often shadowing real installs.
    py = python_path or sys.executable or shutil.which("python3") or shutil.which("python") or "python3"

    server_entry = {
        "command": py,
        "args": ["-m", "codegraph_mcp.server.mcp_server"],
        "env": {},
    }

    # ── Config file locations per tool ────────────────────────────────────────
    system = platform.system()
    home = P.home()

    if system == "Darwin":
        claude_cfg = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        cursor_cfg = home / ".cursor" / "mcp.json"
        zed_cfg = home / ".config" / "zed" / "settings.json"
    elif system == "Windows":
        appdata = P(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        claude_cfg = appdata / "Claude" / "claude_desktop_config.json"
        cursor_cfg = home / ".cursor" / "mcp.json"
        zed_cfg = home / "AppData" / "Roaming" / "Zed" / "settings.json"
    else:  # Linux + other
        cfg_base = P(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        claude_cfg = cfg_base / "Claude" / "claude_desktop_config.json"
        cursor_cfg = home / ".cursor" / "mcp.json"
        zed_cfg = cfg_base / "zed" / "settings.json"

    installed_anywhere = False

    def _merge_json_mcpservers(cfg_path, tool_name, entry):
        """Generic helper: merge entry into {mcpServers: {name: entry}} config."""
        if cfg_path.exists():
            try:
                data = _json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}
        mcps = data.setdefault("mcpServers", {})
        if mcps.get("codegraph-mcp") == entry:
            print(f"  [green]OK[/] {tool_name} - already configured ({cfg_path})")
            return True
        mcps["codegraph-mcp"] = entry
        if not dry_run:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        action = "would write" if dry_run else "wrote"
        print(f"  [green]OK[/] {tool_name} - {action} {cfg_path}")
        return True

    def _merge_zed(cfg_path):
        """Merge into Zed settings.json context_servers section."""
        if cfg_path.exists():
            try:
                data = _json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}
        servers = data.setdefault("context_servers", {})
        zed_entry = {"command": {"path": py, "args": ["-m", "codegraph_mcp.server.mcp_server"]}}
        if servers.get("codegraph-mcp") == zed_entry:
            print(f"  [green]OK[/] Zed - already configured ({cfg_path})")
            return True
        servers["codegraph-mcp"] = zed_entry
        if not dry_run:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        action = "would write" if dry_run else "wrote"
        print(f"  [green]OK[/] Zed - {action} {cfg_path}")
        return True

    print("[bold]CodeGraph MCP - Install[/bold]\n")
    if dry_run:
        print("[yellow]Dry-run mode - no files will be modified.[/yellow]\n")

    # Claude Desktop
    claude_app = any([
        P("/Applications/Claude.app").exists(),
        P(home / "Applications" / "Claude.app").exists(),
        claude_cfg.parent.exists(),
    ])
    if claude_app or dry_run:
        installed_anywhere = _merge_json_mcpservers(claude_cfg, "Claude Desktop", server_entry) or installed_anywhere
    else:
        print("  [dim]Claude Desktop - not detected, skipping[/dim]")

    # Cursor
    cursor_exists = cursor_cfg.parent.exists() or bool(shutil.which("cursor"))
    if cursor_exists or dry_run:
        installed_anywhere = _merge_json_mcpservers(cursor_cfg, "Cursor", server_entry) or installed_anywhere
    else:
        print("  [dim]Cursor - not detected, skipping[/dim]")

    # Zed
    zed_exists = zed_cfg.parent.exists() or bool(shutil.which("zed"))
    if zed_exists or dry_run:
        installed_anywhere = _merge_zed(zed_cfg) or installed_anywhere
    else:
        print("  [dim]Zed - not detected, skipping[/dim]")

    print()
    if installed_anywhere:
        print("[green]Done![/green] Restart your AI tool to load the MCP server.")
        print(f"\n  Server: [bold]{py} -m codegraph_mcp.server.mcp_server[/bold]")
        print("\nNext - index your repo:")
        print("  [bold]codegraph init .[/bold]")
    else:
        print("[yellow]No supported AI tools detected.[/yellow]")
        print("\nManual snippet for claude_desktop_config.json:\n")
        typer.echo(_json.dumps({"mcpServers": {"codegraph-mcp": server_entry}}, indent=2))


if __name__ == "__main__":
    app()
