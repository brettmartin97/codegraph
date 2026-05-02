from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codegraph_mcp.graph.models import (
    ClassNode,
    CodeFile,
    FailureEvent,
    FunctionDescriptor,
    FunctionDiff,
    FunctionEdge,
    FunctionNode,
    FunctionSnapshot,
    Repository,
    RuntimeBinding,
    SnapshotDiff,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
  id TEXT PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  path TEXT NOT NULL,
  language TEXT,
  size_bytes INTEGER,
  line_count INTEGER,
  content_hash TEXT NOT NULL,
  is_test INTEGER,
  is_generated INTEGER,
  is_vendor INTEGER,
  last_indexed_at TEXT NOT NULL,
  UNIQUE(repo_id, path)
);
CREATE TABLE IF NOT EXISTS functions (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  language TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  signature TEXT,
  return_type TEXT,
  parameters_json TEXT NOT NULL,
  decorators_json TEXT NOT NULL,
  annotations_json TEXT NOT NULL,
  visibility TEXT,
  body_hash TEXT NOT NULL,
  signature_hash TEXT NOT NULL,
  descriptor_hash TEXT,
  complexity INTEGER,
  loc INTEGER,
  is_async INTEGER,
  is_generator INTEGER,
  is_test INTEGER,
  parent_symbol_id TEXT,
  enclosing_class TEXT,
  namespace TEXT,
  confidence REAL
);
CREATE TABLE IF NOT EXISTS function_descriptors (
  function_id TEXT PRIMARY KEY,
  raw TEXT,
  summary TEXT,
  params_json TEXT NOT NULL,
  returns TEXT,
  raises_json TEXT NOT NULL,
  side_effects_json TEXT NOT NULL,
  source TEXT NOT NULL,
  quality_score REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS function_edges (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  source_function_id TEXT,
  target_function_id TEXT,
  target_symbol_name TEXT,
  edge_type TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_bindings (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  file_id TEXT,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  target TEXT,
  details_json TEXT NOT NULL,
  confidence REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS index_runs (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  files_seen INTEGER DEFAULT 0,
  functions_seen INTEGER DEFAULT 0,
  edges_seen INTEGER DEFAULT 0,
  diagnostics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_snapshots (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  ref TEXT NOT NULL,
  created_at TEXT NOT NULL,
  summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS function_snapshots (
  id TEXT PRIMARY KEY,
  function_id TEXT NOT NULL,
  repo_id TEXT NOT NULL,
  ref TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  signature_hash TEXT NOT NULL,
  descriptor_hash TEXT,
  qualified_name TEXT NOT NULL,
  signature TEXT,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  caller_count INTEGER NOT NULL DEFAULT 0,
  callee_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS failure_events (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  stack_trace TEXT,
  function_ids_json TEXT NOT NULL,
  file_path TEXT,
  line INTEGER,
  occurred_at TEXT NOT NULL,
  source TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fn_snapshots_repo_ref ON function_snapshots(repo_id, ref);
CREATE INDEX IF NOT EXISTS idx_fn_snapshots_function ON function_snapshots(function_id);
CREATE INDEX IF NOT EXISTS idx_failures_repo ON failure_events(repo_id);
CREATE INDEX IF NOT EXISTS idx_files_repo_path ON files(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_symbols_repo_name ON functions(repo_id, name);
CREATE INDEX IF NOT EXISTS idx_symbols_repo_qname ON functions(repo_id, qualified_name);
CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON function_edges(source_function_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON function_edges(target_function_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_target_symbol ON function_edges(repo_id, target_symbol_name);
CREATE TABLE IF NOT EXISTS classes (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  name TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  bases_json TEXT NOT NULL DEFAULT '[]',
  docstring TEXT,
  is_abstract INTEGER DEFAULT 0,
  decorators_json TEXT NOT NULL DEFAULT '[]',
  loc INTEGER DEFAULT 0,
  method_count INTEGER DEFAULT 0,
  UNIQUE(repo_id, qualified_name)
);
CREATE INDEX IF NOT EXISTS idx_classes_repo ON classes(repo_id);
CREATE INDEX IF NOT EXISTS idx_classes_name ON classes(name);
CREATE VIRTUAL TABLE IF NOT EXISTS functions_fts USING fts5(
  function_id UNINDEXED,
  repo_id UNINDEXED,
  name,
  qualified_name,
  summary,
  purpose,
  tags,
  tokenize="porter unicode61"
);
"""


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate_enrichment()

    def _migrate_enrichment(self):
        """Add enrichment columns if they don't exist (safe to call repeatedly)."""
        for col, defn in [
            ("purpose", "TEXT"),
            ("category", "TEXT"),
            ("importance", "REAL DEFAULT 0.0"),
            ("tags_json", "TEXT DEFAULT '[]'"),
            ("enrichment_source", "TEXT DEFAULT 'none'"),
            ("enriched_at", "TEXT"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE function_descriptors ADD COLUMN {col} {defn}")
            except Exception:
                pass
        self.conn.commit()

    # ── Class nodes ───────────────────────────────────────────────────────────

    def upsert_class(self, cls: ClassNode) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO classes
               (id,repo_id,file_id,name,qualified_name,start_line,end_line,
                bases_json,docstring,is_abstract,decorators_json,loc,method_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cls.id, cls.repo_id, cls.file_id, cls.name, cls.qualified_name,
             cls.start_line, cls.end_line,
             json.dumps(cls.bases), cls.docstring, int(cls.is_abstract),
             json.dumps(cls.decorators), cls.loc, cls.method_count),
        )

    def upsert_classes_bulk(self, classes: list[ClassNode]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO classes
               (id,repo_id,file_id,name,qualified_name,start_line,end_line,
                bases_json,docstring,is_abstract,decorators_json,loc,method_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(c.id, c.repo_id, c.file_id, c.name, c.qualified_name,
              c.start_line, c.end_line,
              json.dumps(c.bases), c.docstring, int(c.is_abstract),
              json.dumps(c.decorators), c.loc, c.method_count)
             for c in classes],
        )
        self.conn.commit()

    def get_class(self, repo_id: str, class_name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM classes WHERE repo_id=? AND (name=? OR qualified_name=? OR qualified_name LIKE ?)",
            (repo_id, class_name, class_name, f"%.{class_name}"),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["bases"] = json.loads(d.pop("bases_json", "[]"))
        d["decorators"] = json.loads(d.pop("decorators_json", "[]"))
        return d

    def list_classes(self, repo_id: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM classes WHERE repo_id=? ORDER BY qualified_name LIMIT ?",
            (repo_id, limit),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["bases"] = json.loads(d.pop("bases_json", "[]"))
            d["decorators"] = json.loads(d.pop("decorators_json", "[]"))
            result.append(d)
        return result

    def get_class_hierarchy(self, repo_id: str) -> list[dict]:
        """Return all inheritance pairs as [{child, parent}]."""
        rows = self.conn.execute(
            "SELECT qualified_name, bases_json FROM classes WHERE repo_id=?", (repo_id,)
        ).fetchall()
        pairs = []
        for row in rows:
            bases = json.loads(row["bases_json"])
            for base in bases:
                pairs.append({"child": row["qualified_name"], "parent": base})
        return pairs

    def class_methods(self, repo_id: str, class_name: str) -> list:
        """Return FunctionNode rows for methods of a class."""
        rows = self.conn.execute(
            "SELECT f.* FROM functions f WHERE f.repo_id=? AND f.enclosing_class=? ORDER BY f.start_line",
            (repo_id, class_name),
        ).fetchall()
        return [self._function_from_row(r) for r in rows]

    # ── Enrichment ────────────────────────────────────────────────────────────

    def update_enrichment(self, function_id: str, purpose: str, category: str,
                          importance: float, tags: list[str], source: str) -> None:
        import datetime
        self.conn.execute(
            """UPDATE function_descriptors
               SET purpose=?, category=?, importance=?, tags_json=?,
                   enrichment_source=?, enriched_at=?
               WHERE function_id=?""",
            (purpose, category, importance, json.dumps(tags), source,
             datetime.datetime.utcnow().isoformat(), function_id),
        )

    def update_enrichment_bulk(self, rows: list[dict]) -> None:
        """rows: list of {function_id, purpose, category, importance, tags, source}"""
        import datetime
        now = datetime.datetime.utcnow().isoformat()
        self.conn.executemany(
            """UPDATE function_descriptors
               SET purpose=?, category=?, importance=?, tags_json=?,
                   enrichment_source=?, enriched_at=?
               WHERE function_id=?""",
            [(r["purpose"], r["category"], r["importance"],
              json.dumps(r.get("tags", [])), r["source"], now, r["function_id"])
             for r in rows],
        )
        self.conn.commit()
        # Sync FTS
        self._rebuild_fts_bulk(rows)

    def functions_needing_enrichment(self, repo_id: str, limit: int = 500) -> list:
        """Return functions whose descriptors have no enrichment yet."""
        rows = self.conn.execute(
            """SELECT f.* FROM functions f
               JOIN function_descriptors fd ON fd.function_id=f.id
               WHERE f.repo_id=? AND f.is_test=0
                 AND (fd.enrichment_source IS NULL OR fd.enrichment_source='none')
               ORDER BY COALESCE(f.complexity,0) DESC
               LIMIT ?""",
            (repo_id, limit),
        ).fetchall()
        return [self._function_from_row(r) for r in rows]

    def enrichment_stats(self, repo_id: str) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM functions WHERE repo_id=? AND is_test=0", (repo_id,)
        ).fetchone()[0]
        enriched = self.conn.execute(
            """SELECT COUNT(*) FROM functions f
               JOIN function_descriptors fd ON fd.function_id=f.id
               WHERE f.repo_id=? AND fd.enrichment_source NOT IN ('none','') AND fd.enrichment_source IS NOT NULL""",
            (repo_id,),
        ).fetchone()[0]
        by_category = {}
        for row in self.conn.execute(
            """SELECT fd.category, COUNT(*) c FROM functions f
               JOIN function_descriptors fd ON fd.function_id=f.id
               WHERE f.repo_id=? AND fd.category IS NOT NULL AND fd.category!=''
               GROUP BY fd.category ORDER BY c DESC""",
            (repo_id,),
        ):
            by_category[row["category"]] = row["c"]
        return {"total_functions": total, "enriched": enriched,
                "pct": round(100 * enriched / max(total, 1), 1), "by_category": by_category}

    # ── FTS5 semantic search ──────────────────────────────────────────────────

    def _rebuild_fts_bulk(self, enriched_rows: list[dict]) -> None:
        """Sync FTS index for a batch of freshly enriched functions."""
        for r in enriched_rows:
            fid = r["function_id"]
            fn_row = self.conn.execute(
                "SELECT repo_id, name, qualified_name FROM functions WHERE id=?", (fid,)
            ).fetchone()
            if not fn_row:
                continue
            fd_row = self.conn.execute(
                "SELECT summary, purpose, tags_json FROM function_descriptors WHERE function_id=?", (fid,)
            ).fetchone()
            summary = fd_row["summary"] if fd_row else ""
            purpose = r.get("purpose", "")
            tags = " ".join(json.loads(fd_row["tags_json"]) if fd_row else [])
            self.conn.execute(
                "DELETE FROM functions_fts WHERE function_id=?", (fid,)
            )
            self.conn.execute(
                "INSERT INTO functions_fts(function_id,repo_id,name,qualified_name,summary,purpose,tags) VALUES (?,?,?,?,?,?,?)",
                (fid, fn_row["repo_id"], fn_row["name"], fn_row["qualified_name"], summary or "", purpose or "", tags),
            )

    def rebuild_fts_for_repo(self, repo_id: str) -> int:
        """Rebuild entire FTS index for a repo. Returns count."""
        self.conn.execute("DELETE FROM functions_fts WHERE repo_id=?", (repo_id,))
        rows = self.conn.execute(
            """SELECT f.id, f.name, f.qualified_name,
                      fd.summary, fd.purpose, fd.tags_json
               FROM functions f
               LEFT JOIN function_descriptors fd ON fd.function_id=f.id
               WHERE f.repo_id=?""",
            (repo_id,),
        ).fetchall()
        data = []
        for row in rows:
            tags = " ".join(json.loads(row["tags_json"] or "[]"))
            data.append((row["id"], repo_id, row["name"], row["qualified_name"],
                         row["summary"] or "", row["purpose"] or "", tags))
        self.conn.executemany(
            "INSERT INTO functions_fts(function_id,repo_id,name,qualified_name,summary,purpose,tags) VALUES (?,?,?,?,?,?,?)",
            data,
        )
        self.conn.commit()
        return len(data)

    def semantic_search(self, repo_id: str, query: str, limit: int = 10) -> list:
        """FTS5 full-text search across name, qualified_name, summary, purpose, tags.
        Multi-word queries use OR semantics so partial matches rank highly.
        """
        # Build FTS5 OR query: strip punctuation, join meaningful words with OR
        import re as _re
        words = [w for w in _re.split(r'\s+', query.strip()) if len(w) > 2]
        fts_query = ' OR '.join(words) if words else query
        try:
            rows = self.conn.execute(
                """SELECT fts.function_id, fts.name, fts.qualified_name,
                          fts.summary, fts.purpose, fts.tags,
                          fd.category,
                          bm25(functions_fts) as score
                   FROM functions_fts fts
                   LEFT JOIN function_descriptors fd ON fd.function_id=fts.function_id
                   WHERE functions_fts MATCH ? AND fts.repo_id=?
                   ORDER BY score LIMIT ?""",
                (fts_query, repo_id, limit),
            ).fetchall()
        except Exception:
            # FTS not populated — fall back to LIKE
            rows = []
        if not rows:
            # Fallback: LIKE on name + summary + purpose
            like = f"%{query}%"
            rows = self.conn.execute(
                """SELECT f.id as function_id, f.name, f.qualified_name,
                          fd.summary, fd.purpose, fd.category, fd.tags_json as tags, 0.5 as score
                   FROM functions f
                   LEFT JOIN function_descriptors fd ON fd.function_id=f.id
                   WHERE f.repo_id=?
                     AND (f.name LIKE ? OR f.qualified_name LIKE ?
                          OR fd.summary LIKE ? OR fd.purpose LIKE ?)
                   LIMIT ?""",
                (repo_id, like, like, like, like, limit),
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("tags"), str):
                try:
                    d["tags"] = json.loads(d["tags"]) if d["tags"].startswith("[") else d["tags"].split()
                except Exception:
                    d["tags"] = []
            results.append(d)
        return results

    def add_repo(self, repo: Repository) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO repositories(id,name,path,created_at) VALUES (?,?,?,?)",
            (repo.id, repo.name, repo.path, repo.created_at),
        )
        self.conn.commit()

    def get_repo(self, name: str) -> Repository | None:
        row = self.conn.execute("SELECT * FROM repositories WHERE name=?", (name,)).fetchone()
        return Repository(**dict(row)) if row else None

    def list_repos(self) -> list[Repository]:
        return [Repository(**dict(r)) for r in self.conn.execute("SELECT * FROM repositories ORDER BY name")]

    def upsert_file(self, f: CodeFile) -> None:
        # Remove any legacy record for the same logical path with different separators
        # (e.g. old Windows backslash paths). This prevents duplicate file entries
        # that cause function_at() to resolve to stale function IDs with no edges.
        stale = self.conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND REPLACE(path,'\\','/') = ? AND id != ?",
            (f.repo_id, f.path.replace("\\", "/"), f.id),
        ).fetchall()
        for row in stale:
            self.delete_file(row["id"])
        self.conn.execute(
            """INSERT OR REPLACE INTO files(id,repo_id,path,language,size_bytes,line_count,content_hash,is_test,is_generated,is_vendor,last_indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f.id, f.repo_id, f.path, f.language, f.size_bytes, f.line_count, f.content_hash, int(f.is_test), int(f.is_generated), int(f.is_vendor), f.last_indexed_at),
        )

    def delete_file(self, file_id: str) -> None:
        """Remove a file and all its functions/edges/bindings from the graph.
        Called when a file is deleted from disk (watch mode or incremental cleanup).
        """
        existing = [r["id"] for r in self.conn.execute(
            "SELECT id FROM functions WHERE file_id=?", (file_id,)
        )]
        if existing:
            q = ",".join("?" for _ in existing)
            self.conn.execute(
                f"DELETE FROM function_descriptors WHERE function_id IN ({q})", existing
            )
            self.conn.execute(
                f"DELETE FROM function_edges WHERE source_function_id IN ({q})", existing
            )
        self.conn.execute("DELETE FROM functions WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM runtime_bindings WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM classes WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM files WHERE id=?", (file_id,))
        self.conn.commit()

    def replace_file_analysis(self, file_id: str, functions: list[FunctionNode], edges: list[FunctionEdge], runtime_bindings: list[RuntimeBinding]) -> None:
        existing = [r["id"] for r in self.conn.execute("SELECT id FROM functions WHERE file_id=?", (file_id,))]
        if existing:
            q = ",".join("?" for _ in existing)
            self.conn.execute(f"DELETE FROM function_descriptors WHERE function_id IN ({q})", existing)
            self.conn.execute(f"DELETE FROM function_edges WHERE source_function_id IN ({q})", existing)
        self.conn.execute("DELETE FROM functions WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM runtime_bindings WHERE file_id=?", (file_id,))
        for fn in functions:
            self.upsert_function(fn)
        for edge in edges:
            self.upsert_edge(edge)
        for rb in runtime_bindings:
            self.upsert_runtime_binding(rb)
        self.conn.commit()

    def upsert_function(self, fn: FunctionNode) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO functions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fn.id, fn.repo_id, fn.file_id, fn.language, str(fn.kind), fn.name, fn.qualified_name, fn.display_name,
             fn.start_line, fn.end_line, fn.signature, fn.return_type, json.dumps(fn.parameters_json), json.dumps(fn.decorators),
             json.dumps(fn.annotations), fn.visibility, fn.body_hash, fn.signature_hash, fn.descriptor_hash, fn.complexity, fn.loc,
             int(fn.is_async), int(fn.is_generator), int(fn.is_test), fn.parent_symbol_id, fn.enclosing_class, fn.namespace, fn.confidence),
        )
        if fn.descriptor:
            d = fn.descriptor
            self.conn.execute(
                """INSERT OR REPLACE INTO function_descriptors
                   (function_id,raw,summary,params_json,returns,raises_json,
                    side_effects_json,source,quality_score)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (fn.id, d.raw, d.summary, json.dumps(d.params), d.returns,
                 json.dumps(d.raises), json.dumps(d.side_effects), d.source, d.quality_score),
            )

    def upsert_edge(self, edge: FunctionEdge) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO function_edges VALUES(?,?,?,?,?,?,?,?,?)""",
            (edge.id, edge.repo_id, edge.source_function_id, edge.target_function_id, edge.target_symbol_name, str(edge.edge_type), edge.confidence, json.dumps(edge.evidence), edge.created_at),
        )

    def upsert_runtime_binding(self, rb: RuntimeBinding) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO runtime_bindings VALUES(?,?,?,?,?,?,?,?)""",
            (rb.id, rb.repo_id, rb.file_id, rb.kind, rb.name, rb.target, json.dumps(rb.details), rb.confidence),
        )

    def resolve_edges(self, repo_id: str) -> None:
        # Build name->id lookup in one query instead of one query per edge
        fn_rows = self.conn.execute(
            "SELECT id, name, qualified_name FROM functions WHERE repo_id=?", (repo_id,)
        ).fetchall()
        # name -> [id, ...] and qname_suffix -> [id, ...]
        by_name: dict[str, list[str]] = {}
        by_qsuffix: dict[str, list[str]] = {}
        for r in fn_rows:
            by_name.setdefault(r["name"], []).append(r["id"])
            # last segment of qualified name (e.g. "Foo.bar" -> "bar")
            suffix = r["qualified_name"].rsplit(".", 1)[-1]
            by_qsuffix.setdefault(suffix, []).append(r["id"])

        edge_rows = self.conn.execute(
            "SELECT id, target_symbol_name FROM function_edges "
            "WHERE repo_id=? AND target_function_id IS NULL AND target_symbol_name IS NOT NULL",
            (repo_id,),
        ).fetchall()
        updates: list[tuple[str, str]] = []
        for row in edge_rows:
            sym = row["target_symbol_name"]
            candidates = by_name.get(sym) or by_qsuffix.get(sym.rsplit(".", 1)[-1], [])
            if len(candidates) == 1:
                updates.append((candidates[0], row["id"]))
        if updates:
            self.conn.executemany(
                "UPDATE function_edges SET target_function_id=?, confidence=MAX(confidence, 0.7) WHERE id=?",
                updates,
            )
        self.conn.commit()

    def find_functions(self, repo_id: str, query: str, limit: int = 20) -> list[FunctionNode]:
        like = f"%{query}%"
        rows = self.conn.execute(
            """SELECT * FROM functions WHERE repo_id=? AND (name LIKE ? OR qualified_name LIKE ? OR signature LIKE ?) ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, loc DESC LIMIT ?""",
            (repo_id, like, like, like, query, limit),
        ).fetchall()
        return [self._function_from_row(r) for r in rows]

    def get_function(self, function_id: str) -> FunctionNode | None:
        row = self.conn.execute("SELECT * FROM functions WHERE id=?", (function_id,)).fetchone()
        return self._function_from_row(row) if row else None

    def get_function_by_name(self, repo_id: str, name: str) -> FunctionNode | None:
        row = self.conn.execute("SELECT * FROM functions WHERE repo_id=? AND (qualified_name=? OR name=?) ORDER BY LENGTH(qualified_name) LIMIT 1", (repo_id, name, name)).fetchone()
        return self._function_from_row(row) if row else None

    def callers(self, function_id: str) -> list[FunctionNode]:
        rows = self.conn.execute("SELECT f.* FROM function_edges e JOIN functions f ON f.id=e.source_function_id WHERE e.target_function_id=? AND e.edge_type='CALLS'", (function_id,)).fetchall()
        return [self._function_from_row(r) for r in rows]

    def callees(self, function_id: str) -> list[FunctionNode]:
        rows = self.conn.execute("SELECT f.* FROM function_edges e JOIN functions f ON f.id=e.target_function_id WHERE e.source_function_id=? AND e.edge_type='CALLS'", (function_id,)).fetchall()
        return [self._function_from_row(r) for r in rows]

    def unresolved_callees(self, function_id: str) -> list[FunctionEdge]:
        rows = self.conn.execute("SELECT * FROM function_edges WHERE source_function_id=? AND edge_type='CALLS' AND target_function_id IS NULL", (function_id,)).fetchall()
        return [self._edge_from_row(r) for r in rows]

    def related_tests(self, repo_id: str, fn: FunctionNode, limit: int = 10) -> list[FunctionNode]:
        """Find tests related to fn using three strategies (in priority order):
        1. Call-graph: test functions that directly call fn (or transitively reach it).
        2. TESTS edges: explicit TESTS-typed edges emitted by analyzers.
        3. Name-convention: test function/file names that contain fn.name or fn.enclosing_class.
        Results are deduplicated and ranked by strategy confidence.
        """
        seen: set[str] = set()
        results: list[tuple[float, FunctionNode]] = []

        def _add(f: FunctionNode, score: float):
            if f.id not in seen:
                seen.add(f.id)
                results.append((score, f))

        # Strategy 1: call-graph — test callers (direct + transitive up to depth 3)
        frontier = [fn.id]
        visited_cg: set[str] = {fn.id}
        for depth in range(3):
            next_frontier: list[str] = []
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            caller_rows = self.conn.execute(
                f"SELECT f.* FROM function_edges e JOIN functions f ON f.id=e.source_function_id "
                f"WHERE e.target_function_id IN ({placeholders}) AND e.edge_type='CALLS' AND f.is_test=1",
                frontier,
            ).fetchall()
            for row in caller_rows:
                f = self._function_from_row(row)
                _add(f, 1.0 - depth * 0.15)  # direct=1.0, depth1=0.85, depth2=0.70
            # Also expand non-test callers for next hop
            all_caller_rows = self.conn.execute(
                f"SELECT e.source_function_id FROM function_edges e "
                f"WHERE e.target_function_id IN ({placeholders}) AND e.edge_type='CALLS'",
                frontier,
            ).fetchall()
            for row in all_caller_rows:
                fid = row["source_function_id"]
                if fid and fid not in visited_cg:
                    visited_cg.add(fid)
                    next_frontier.append(fid)
            frontier = next_frontier

        # Strategy 2: TESTS edges
        tests_rows = self.conn.execute(
            "SELECT f.* FROM function_edges e JOIN functions f ON f.id=e.source_function_id "
            "WHERE e.target_function_id=? AND e.edge_type='TESTS'",
            (fn.id,),
        ).fetchall()
        for row in tests_rows:
            _add(self._function_from_row(row), 0.95)

        # Strategy 3: name-convention fallback
        terms = [t for t in [fn.name.lower(), (fn.enclosing_class or "").lower()] if len(t) > 3]
        if terms and len(results) < limit:
            name_rows = self.conn.execute(
                "SELECT * FROM functions WHERE repo_id=? AND is_test=1 LIMIT 500", (repo_id,)
            ).fetchall()
            for row in name_rows:
                f = self._function_from_row(row)
                if f.id in seen:
                    continue
                hay = f"{f.qualified_name} {self.file_path(f.file_id)}".lower()
                score = sum(0.4 for t in terms if t and t in hay)
                if score:
                    _add(f, score)

        return [f for _, f in sorted(results, key=lambda x: x[0], reverse=True)[:limit]]

    def runtime_bindings(self, repo_id: str) -> list[RuntimeBinding]:
        rows = self.conn.execute("SELECT * FROM runtime_bindings WHERE repo_id=?", (repo_id,)).fetchall()
        return [self._runtime_from_row(r) for r in rows]

    def all_resolved_edges(self, repo_id: str) -> list[FunctionEdge]:
        """Return all edges for *repo_id* where both source and target are resolved."""
        rows = self.conn.execute(
            "SELECT * FROM function_edges WHERE repo_id=? AND target_function_id IS NOT NULL",
            (repo_id,),
        ).fetchall()
        return [self._edge_from_row(r) for r in rows]

    def files(self, repo_id: str):
        return self.conn.execute("SELECT * FROM files WHERE repo_id=? ORDER BY path", (repo_id,)).fetchall()
    # ── Phase 13: Failure memory ──────────────────────────────────

    def add_failure_event(self, event: FailureEvent) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO failure_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.id, event.repo_id, event.kind, event.message, event.stack_trace,
                json.dumps(event.function_ids), event.file_path, event.line,
                event.occurred_at, event.source, json.dumps(event.metadata),
            ),
        )
        self.conn.commit()

    def failures_for_function(self, function_id: str) -> list[FailureEvent]:
        rows = self.conn.execute(
            "SELECT * FROM failure_events WHERE function_ids_json LIKE ?",
            (f'%"{function_id}"%',),
        ).fetchall()
        return [self._failure_from_row(r) for r in rows]

    def recent_failures(self, repo_id: str, limit: int = 20) -> list[FailureEvent]:
        rows = self.conn.execute(
            "SELECT * FROM failure_events WHERE repo_id=? ORDER BY occurred_at DESC LIMIT ?",
            (repo_id, limit),
        ).fetchall()
        return [self._failure_from_row(r) for r in rows]

    def _failure_from_row(self, r) -> FailureEvent:
        return FailureEvent(
            id=r["id"], repo_id=r["repo_id"], kind=r["kind"],
            message=r["message"], stack_trace=r["stack_trace"],
            function_ids=json.loads(r["function_ids_json"]),
            file_path=r["file_path"], line=r["line"],
            occurred_at=r["occurred_at"], source=r["source"],
            metadata=json.loads(r["metadata_json"]),
        )

    # ── Phase 14: Single-line lookup ──────────────────────────────────────────

    def function_at(self, repo_id: str, rel_path: str, line: int) -> FunctionNode | None:
        """Return the innermost (smallest span) function containing *line*, or None."""
        row = self.conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND path=?", (repo_id, rel_path)
        ).fetchone()
        if not row:
            return None
        rows = self.conn.execute(
            """SELECT * FROM functions
               WHERE file_id=? AND start_line <= ? AND end_line >= ?
               ORDER BY (end_line - start_line) ASC""",
            (row["id"], line, line),
        ).fetchall()
        return self._function_from_row(rows[0]) if rows else None

    def functions_at_lines(self, repo_id: str, rel_path: str, changed_lines: set) -> list[FunctionNode]:
        """Return functions whose body overlaps any of the given line numbers."""
        if not changed_lines:
            return []
        row = self.conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND path=?", (repo_id, rel_path)
        ).fetchone()
        if not row:
            return []
        file_id = row["id"]
        min_line, max_line = min(changed_lines), max(changed_lines)
        rows = self.conn.execute(
            """SELECT * FROM functions
               WHERE file_id=?
                 AND start_line <= ? AND end_line >= ?""",
            (file_id, max_line, min_line),
        ).fetchall()
        return [self._function_from_row(r) for r in rows]


    def graph_freshness(self, repo_id: str) -> dict:
        """Return indexing recency info for the repo."""
        row = self.conn.execute(
            "SELECT MAX(last_indexed_at) as latest, COUNT(*) as file_count "
            "FROM files WHERE repo_id=?", (repo_id,)
        ).fetchone()
        if not row or not row["latest"]:
            return {"indexed": False, "hours_stale": None, "file_count": 0, "warning": None}
        from datetime import datetime, timezone
        try:
            last = datetime.fromisoformat(row["latest"].replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        except ValueError:
            return {"indexed": True, "hours_stale": None, "file_count": row["file_count"], "warning": None}
        warning = None
        if hours > 72:
            warning = f"Graph is {hours:.0f}h stale — run `codegraph index <repo> --mode incremental` to refresh."
        elif hours > 24:
            warning = f"Graph is {hours:.0f}h old — consider refreshing with `codegraph index <repo>`."
        return {
            "indexed": True,
            "last_indexed_at": row["latest"],
            "hours_stale": round(hours, 1),
            "file_count": row["file_count"],
            "warning": warning,
        }

    def file_path(self, file_id: str) -> str:
        row = self.conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
        return row["path"] if row else "<unknown>"

    def overview(self, repo_id: str) -> dict:
        langs = {r["language"] or "unknown": r["c"] for r in self.conn.execute(
            "SELECT language, COUNT(*) c FROM files WHERE repo_id=? GROUP BY language", (repo_id,)
        )}
        function_count = self.conn.execute(
            "SELECT COUNT(*) c FROM functions WHERE repo_id=?", (repo_id,)
        ).fetchone()["c"]
        file_count = self.conn.execute(
            "SELECT COUNT(*) c FROM files WHERE repo_id=?", (repo_id,)
        ).fetchone()["c"]
        high_complexity = [dict(r) for r in self.conn.execute(
            "SELECT qualified_name, complexity, loc, file_id FROM functions "
            "WHERE repo_id=? ORDER BY COALESCE(complexity,0) DESC, loc DESC LIMIT 10",
            (repo_id,),
        )]
        for item in high_complexity:
            item["file"] = self.file_path(item.pop("file_id"))
        descriptor_coverage = self.conn.execute(
            "SELECT AVG(CASE WHEN fd.quality_score > 0.5 THEN 1.0 ELSE 0 END) c "
            "FROM functions f LEFT JOIN function_descriptors fd ON fd.function_id=f.id "
            "WHERE f.repo_id=?",
            (repo_id,),
        ).fetchone()["c"] or 0.0
        return {
            "file_count": file_count,
            "function_count": function_count,
            "languages": langs,
            "descriptor_coverage": descriptor_coverage,
            "high_complexity": high_complexity,
        }

    def _function_from_row(self, r) -> FunctionNode:
        drow = self.conn.execute(
            "SELECT * FROM function_descriptors WHERE function_id=?", (r["id"],)
        ).fetchone()
        descriptor = None
        if drow:
            descriptor = FunctionDescriptor(
                function_id=r["id"],
                raw=drow["raw"],
                summary=drow["summary"],
                params=json.loads(drow["params_json"]),
                returns=drow["returns"],
                raises=json.loads(drow["raises_json"]),
                side_effects=json.loads(drow["side_effects_json"]),
                source=drow["source"],
                quality_score=drow["quality_score"],
                purpose=drow["purpose"] if "purpose" in drow.keys() else None,
                category=drow["category"] if "category" in drow.keys() else None,
                importance=drow["importance"] if "importance" in drow.keys() else 0.0,
                tags=json.loads(drow["tags_json"]) if "tags_json" in drow.keys() and drow["tags_json"] else [],
            )
        return FunctionNode(
            id=r["id"],
            repo_id=r["repo_id"],
            file_id=r["file_id"],
            language=r["language"],
            kind=r["kind"],
            name=r["name"],
            qualified_name=r["qualified_name"],
            display_name=r["display_name"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            signature=r["signature"],
            return_type=r["return_type"],
            parameters=json.loads(r["parameters_json"]),
            decorators=json.loads(r["decorators_json"]),
            annotations=json.loads(r["annotations_json"]),
            visibility=r["visibility"],
            body_hash=r["body_hash"],
            signature_hash=r["signature_hash"],
            descriptor_hash=r["descriptor_hash"],
            complexity=r["complexity"],
            loc=r["loc"],
            is_async=bool(r["is_async"]),
            is_generator=bool(r["is_generator"]),
            is_test=bool(r["is_test"]),
            parent_symbol_id=r["parent_symbol_id"],
            enclosing_class=r["enclosing_class"],
            namespace=r["namespace"],
            confidence=r["confidence"] or 0.0,
            descriptor=descriptor,
        )

    def _edge_from_row(self, r) -> FunctionEdge:
        return FunctionEdge(
            id=r["id"],
            repo_id=r["repo_id"],
            source_function_id=r["source_function_id"],
            target_function_id=r["target_function_id"],
            target_symbol_name=r["target_symbol_name"],
            edge_type=r["edge_type"],
            confidence=r["confidence"],
            evidence=json.loads(r["evidence_json"]),
        )

    def _runtime_from_row(self, r) -> RuntimeBinding:
        return RuntimeBinding(
            id=r["id"],
            repo_id=r["repo_id"],
            file_id=r["file_id"],
            kind=r["kind"],
            name=r["name"],
            target=r["target"],
            details=json.loads(r["details_json"]),
            confidence=r["confidence"],
        )

    def transitive_callers(
        self, function_id: str, depth: int = 3, min_confidence: float = 0.0
    ) -> list[FunctionNode]:
        visited: set[str] = set()
        frontier: set[str] = {function_id}
        result: list[FunctionNode] = []
        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for fid in frontier:
                rows = self.conn.execute(
                    "SELECT source_function_id FROM function_edges "
                    "WHERE target_function_id=? AND confidence>=? AND source_function_id IS NOT NULL",
                    (fid, min_confidence),
                ).fetchall()
                for row in rows:
                    sid = row["source_function_id"]
                    if sid not in visited and sid != function_id:
                        visited.add(sid)
                        fn_row = self.conn.execute("SELECT * FROM functions WHERE id=?", (sid,)).fetchone()
                        if fn_row:
                            result.append(self._function_from_row(fn_row))
                            next_frontier.add(sid)
            frontier = next_frontier
        return result

    def transitive_callees(
        self, function_id: str, depth: int = 3, min_confidence: float = 0.0
    ) -> list[FunctionNode]:
        visited: set[str] = set()
        frontier: set[str] = {function_id}
        result: list[FunctionNode] = []
        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for fid in frontier:
                rows = self.conn.execute(
                    "SELECT target_function_id FROM function_edges "
                    "WHERE source_function_id=? AND confidence>=? AND target_function_id IS NOT NULL",
                    (fid, min_confidence),
                ).fetchall()
                for row in rows:
                    tid = row["target_function_id"]
                    if tid not in visited and tid != function_id:
                        visited.add(tid)
                        fn_row = self.conn.execute("SELECT * FROM functions WHERE id=?", (tid,)).fetchone()
                        if fn_row:
                            result.append(self._function_from_row(fn_row))
                            next_frontier.add(tid)
            frontier = next_frontier
        return result

    def save_function_snapshot(self, fn: FunctionNode, ref: str, caller_count: int = 0, callee_count: int = 0) -> None:
        from codegraph_mcp.utils import stable_id, utcnow
        snap_id = stable_id(fn.id, ref)
        self.conn.execute(
            "INSERT OR REPLACE INTO function_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snap_id, fn.id, fn.repo_id, ref, utcnow(),
                fn.body_hash, fn.signature_hash, fn.descriptor_hash,
                fn.qualified_name, fn.signature, fn.start_line, fn.end_line,
                caller_count, callee_count,
            ),
        )
        self.conn.commit()

    def get_function_snapshots(self, repo_id: str, ref: str) -> dict[str, FunctionSnapshot]:
        rows = self.conn.execute(
            "SELECT * FROM function_snapshots WHERE repo_id=? AND ref=?", (repo_id, ref)
        ).fetchall()
        return {
            r["function_id"]: FunctionSnapshot(
                id=r["id"],
                function_id=r["function_id"],
                repo_id=r["repo_id"],
                ref=r["ref"],
                captured_at=r["captured_at"],
                body_hash=r["body_hash"],
                signature_hash=r["signature_hash"],
                descriptor_hash=r["descriptor_hash"],
                qualified_name=r["qualified_name"],
                signature=r["signature"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                caller_count=r["caller_count"],
                callee_count=r["callee_count"],
            )
            for r in rows
        }

    def snapshot_repo(self, repo_id: str, ref: str) -> int:
        """Snapshot all functions for repo_id under the given ref label. Returns count."""
        rows = self.conn.execute(
            "SELECT * FROM functions WHERE repo_id=?", (repo_id,)
        ).fetchall()
        from codegraph_mcp.utils import stable_id, utcnow
        now = utcnow()
        records = []
        for r in rows:
            caller_count = self.conn.execute(
                "SELECT COUNT(*) c FROM function_edges WHERE target_function_id=?", (r["id"],)
            ).fetchone()["c"]
            callee_count = self.conn.execute(
                "SELECT COUNT(*) c FROM function_edges WHERE source_function_id=?", (r["id"],)
            ).fetchone()["c"]
            records.append((
                stable_id(r["id"], ref), r["id"], repo_id, ref, now,
                r["body_hash"], r["signature_hash"], r["descriptor_hash"],
                r["qualified_name"], r["signature"], r["start_line"], r["end_line"],
                caller_count, callee_count,
            ))
        self.conn.executemany(
            "INSERT OR REPLACE INTO function_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        self.conn.commit()
        return len(records)

    def diff_snapshots(self, repo_id: str, ref_a: str, ref_b: str) -> SnapshotDiff:
        snaps_a = self.get_function_snapshots(repo_id, ref_a)
        snaps_b = self.get_function_snapshots(repo_id, ref_b)
        ids_a, ids_b = set(snaps_a), set(snaps_b)
        added: list[FunctionDiff] = []
        removed: list[FunctionDiff] = []
        changed: list[FunctionDiff] = []
        for fid in ids_b - ids_a:
            s = snaps_b[fid]
            row = self.conn.execute(
                "SELECT path FROM files WHERE id=(SELECT file_id FROM functions WHERE id=?)", (fid,)
            ).fetchone()
            added.append(FunctionDiff(
                function_id=fid, qualified_name=s.qualified_name,
                file=row["path"] if row else "<unknown>", change_type="added",
            ))
        for fid in ids_a - ids_b:
            s = snaps_a[fid]
            row = self.conn.execute(
                "SELECT path FROM files WHERE id=(SELECT file_id FROM functions WHERE id=?)", (fid,)
            ).fetchone()
            removed.append(FunctionDiff(
                function_id=fid, qualified_name=s.qualified_name,
                file=row["path"] if row else "<unknown>", change_type="removed",
            ))
        for fid in ids_a & ids_b:
            sa, sb = snaps_a[fid], snaps_b[fid]
            body_changed = sa.body_hash != sb.body_hash
            sig_changed = sa.signature_hash != sb.signature_hash
            desc_changed = sa.descriptor_hash != sb.descriptor_hash
            if body_changed or sig_changed or desc_changed:
                row = self.conn.execute(
                    "SELECT path FROM files WHERE id=(SELECT file_id FROM functions WHERE id=?)", (fid,)
                ).fetchone()
                changed.append(FunctionDiff(
                    function_id=fid, qualified_name=sb.qualified_name,
                    file=row["path"] if row else "<unknown>", change_type="changed",
                    body_changed=body_changed, signature_changed=sig_changed,
                    descriptor_changed=desc_changed,
                ))
        return SnapshotDiff(
            repo=repo_id, ref_a=ref_a, ref_b=ref_b,
            functions_added=added, functions_removed=removed, functions_changed=changed,
            summary={"added": len(added), "removed": len(removed), "changed": len(changed)},
        )
