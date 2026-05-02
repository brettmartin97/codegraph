from __future__ import annotations

from pathlib import Path

from codegraph_mcp.analyzers.base import AnalysisResult, SourceFile
from codegraph_mcp.analyzers.registry import AnalyzerRegistry
from codegraph_mcp.config import settings
from codegraph_mcp.graph.models import CodeFile, Repository
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.repo_walker import RepoWalker
from codegraph_mcp.security.path_jail import ensure_within
from codegraph_mcp.utils import (
    detect_language,
    extract_zip,
    is_probably_test,
    sha256_file,
    stable_id,
)


class Indexer:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self.registry = AnalyzerRegistry()
        self.walker = RepoWalker(settings.max_file_bytes)

    def _analyze_source(self, source: SourceFile) -> AnalysisResult:
        combined = AnalysisResult()
        for analyzer in self.registry.get_all(source.language, source.path):
            analysis = analyzer.analyze(source)
            combined.functions.extend(analysis.functions)
            combined.classes.extend(analysis.classes)
            combined.edges.extend(analysis.edges)
            combined.runtime_bindings.extend(analysis.runtime_bindings)
            combined.diagnostics.extend(analysis.diagnostics)
        return combined

    def add_repo(self, name: str, path: Path) -> Repository:
        if settings.allow_external_repos:
            resolved = Path(path).resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Path does not exist: {resolved}")
        else:
            resolved = ensure_within(settings.repo_root, path)
        repo = Repository(id=stable_id("repo", name, resolved), name=name, path=str(resolved))
        self.store.add_repo(repo)
        return repo

    def add_repo_from_zip(self, name: str, zip_path: Path) -> Repository:
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            raise FileNotFoundError(f"Zip archive not found: {zip_path}")
        dest = (settings.repo_root / name).resolve()
        repo_dir = extract_zip(zip_path, dest)
        return self.add_repo(name, repo_dir)

    def index_repo(self, repo_name: str, mode: str = "full") -> dict:
        repo = self.store.get_repo(repo_name)
        if not repo:
            raise ValueError(f"Unknown repo: {repo_name}")
        if settings.allow_external_repos:
            repo_path = Path(repo.path).resolve()
        else:
            repo_path = ensure_within(settings.repo_root, Path(repo.path))

        # Build hash cache for incremental mode
        existing_hashes: dict[str, str] = {}
        if mode == "incremental":
            for row in self.store.files(repo.id):
                existing_hashes[row["path"].replace("\\", "/")] = row["content_hash"]

        files_seen = functions_seen = edges_seen = runtime_seen = 0
        files_skipped = 0
        diagnostics: list[str] = []

        for path in self.walker.iter_files(repo_path):
            rel = path.relative_to(repo_path).as_posix()
            lang = detect_language(path)
            if not lang:
                continue

            # Incremental: skip files whose content hash hasn't changed
            if mode == "incremental":
                current_hash = sha256_file(path)
                if existing_hashes.get(rel) == current_hash:
                    files_skipped += 1
                    continue

            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as exc:
                diagnostics.append(f"Failed reading {rel}: {exc}")
                continue

            file_id = stable_id(repo.id, rel)
            cf = CodeFile(
                id=file_id, repo_id=repo.id, path=rel, language=lang,
                size_bytes=path.stat().st_size, line_count=text.count("\n") + 1,
                content_hash=sha256_file(path), is_test=is_probably_test(path),
                is_generated="generated" in rel.lower(),
                is_vendor="vendor" in rel.lower(),
            )
            self.store.upsert_file(cf)
            if self.registry.get_all(lang, path):
                source = SourceFile(
                    repo_id=repo.id, file_id=file_id, path=path,
                    relative_path=rel, language=lang, text=text,
                )
                analysis = self._analyze_source(source)
                self.store.replace_file_analysis(
                    file_id, analysis.functions, analysis.edges,
                    analysis.runtime_bindings,
                )
                if analysis.classes:
                    self.store.upsert_classes_bulk(analysis.classes)
                functions_seen += len(analysis.functions)
                edges_seen += len(analysis.edges)
                runtime_seen += len(analysis.runtime_bindings)
                diagnostics.extend(f"{rel}: {d}" for d in analysis.diagnostics)
            files_seen += 1

        # Incremental: purge files that no longer exist on disk
        if mode == "incremental":
            files_removed = 0
            for row in self.store.files(repo.id):
                disk_path = repo_path / row["path"]
                if not disk_path.exists():
                    self.store.delete_file(row["id"])
                    files_removed += 1

        self.store.resolve_edges(repo.id)
        try:
            self.store.snapshot_repo(repo.id, "latest")
        except Exception as exc:
            diagnostics.append(f"Snapshot warning: {exc}")

        return {
            "repo": repo.name, "mode": mode,
            "files_seen": files_seen,
            "files_skipped": files_skipped,
            "functions_seen": functions_seen,
            "edges_seen": edges_seen,
            "runtime_bindings_seen": runtime_seen,
            "diagnostics": diagnostics[:200],
        }

    def remove_file(self, repo_name: str, file_path: Path) -> dict:
        """Remove a deleted file from the graph. Called by watch mode on deletion."""
        repo = self.store.get_repo(repo_name)
        if not repo:
            raise ValueError(f"Unknown repo: {repo_name}")
        repo_path = Path(repo.path).resolve()
        if not file_path.is_absolute():
            file_path = repo_path / file_path
        try:
            rel = file_path.relative_to(repo_path).as_posix()
        except ValueError:
            return {"skipped": True, "reason": "path outside repo", "file": str(file_path)}
        file_id = stable_id(repo.id, rel)
        self.store.delete_file(file_id)
        return {"file": rel, "removed": True}

    def index_file(self, repo_name: str, file_path: Path) -> dict:
        """Re-index a single file. Used by watch mode."""
        repo = self.store.get_repo(repo_name)
        if not repo:
            raise ValueError(f"Unknown repo: {repo_name}")
        repo_path = Path(repo.path).resolve()

        if not file_path.is_absolute():
            file_path = repo_path / file_path
        rel = file_path.relative_to(repo_path).as_posix()
        lang = detect_language(file_path)
        if not lang:
            return {"skipped": True, "reason": "unsupported language", "file": rel}

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return {"error": str(exc), "file": rel}

        file_id = stable_id(repo.id, rel)
        cf = CodeFile(
            id=file_id, repo_id=repo.id, path=rel, language=lang,
            size_bytes=file_path.stat().st_size,
            line_count=text.count("\n") + 1,
            content_hash=sha256_file(file_path),
            is_test=is_probably_test(file_path),
            is_generated="generated" in rel.lower(),
            is_vendor="vendor" in rel.lower(),
        )
        self.store.upsert_file(cf)
        functions_seen = edges_seen = runtime_seen = 0
        if self.registry.get_all(lang, file_path):
            source = SourceFile(
                repo_id=repo.id, file_id=file_id, path=file_path,
                relative_path=rel, language=lang, text=text,
            )
            analysis = self._analyze_source(source)
            self.store.replace_file_analysis(
                file_id, analysis.functions, analysis.edges,
                analysis.runtime_bindings,
            )
            if analysis.classes:
                self.store.upsert_classes_bulk(analysis.classes)
            functions_seen = len(analysis.functions)
            edges_seen = len(analysis.edges)
            runtime_seen = len(analysis.runtime_bindings)
        self.store.resolve_edges(repo.id)
        return {
            "file": rel,
            "functions_seen": functions_seen,
            "edges_seen": edges_seen,
            "runtime_bindings_seen": runtime_seen,
        }
