from __future__ import annotations

import re

import yaml

from codegraph_mcp.analyzers.base import AnalysisResult, LanguageAnalyzer, SourceFile
from codegraph_mcp.graph.models import RuntimeBinding
from codegraph_mcp.utils import stable_id


class RuntimeAnalyzer(LanguageAnalyzer):
    language = "runtime"

    def supports(self, language: str, path):
        return language in {"yaml", "compose", "dockerfile", "helm", "kubernetes", "json", "terraform"}

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        if source.language == "dockerfile":
            self._dockerfile(source, result)
        elif source.language in {"yaml", "compose", "helm", "kubernetes"}:
            self._yaml(source, result)
        return result

    def _dockerfile(self, source: SourceFile, result: AnalysisResult) -> None:
        for idx, line in enumerate(source.text.splitlines(), start=1):
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith(("CMD", "ENTRYPOINT", "EXPOSE", "HEALTHCHECK", "FROM")):
                kind = upper.split()[0].lower()
                result.runtime_bindings.append(RuntimeBinding(
                    id=stable_id(source.repo_id, source.relative_path, kind, idx), repo_id=source.repo_id,
                    file_id=source.file_id, kind=kind, name=stripped[:80], target=None,
                    details={"line": idx, "raw": stripped}, confidence=0.9,
                ))

    def _yaml(self, source: SourceFile, result: AnalysisResult) -> None:
        try:
            docs = list(yaml.safe_load_all(source.text))
        except Exception as exc:
            if source.language == "helm":
                self._templated_yaml(source, result)
            else:
                result.diagnostics.append(f"YAML parse error: {exc}")
            return
        for doc_idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                continue
            if "services" in doc and isinstance(doc["services"], dict):
                for name, svc in doc["services"].items():
                    result.runtime_bindings.append(RuntimeBinding(
                        id=stable_id(source.repo_id, source.relative_path, "compose_service", name), repo_id=source.repo_id,
                        file_id=source.file_id, kind="compose_service", name=name, target=str(svc.get("command") or svc.get("image") or svc.get("build")),
                        details=svc, confidence=0.95,
                    ))
            kind = doc.get("kind")
            metadata = doc.get("metadata") or {}
            if kind:
                name = metadata.get("name", f"doc-{doc_idx}")
                result.runtime_bindings.append(RuntimeBinding(
                    id=stable_id(source.repo_id, source.relative_path, kind, name), repo_id=source.repo_id,
                    file_id=source.file_id, kind=f"k8s_{kind.lower()}", name=name,
                    target=self._container_hint(doc), details={"kind": kind, "metadata": metadata}, confidence=0.85,
                ))
            if "jobs" in doc and source.relative_path.startswith(".github/workflows"):
                for name, job in (doc.get("jobs") or {}).items():
                    result.runtime_bindings.append(RuntimeBinding(
                        id=stable_id(source.repo_id, source.relative_path, "gha_job", name), repo_id=source.repo_id,
                        file_id=source.file_id, kind="github_action_job", name=name, target=None, details=job, confidence=0.9,
                    ))

    def _templated_yaml(self, source: SourceFile, result: AnalysisResult) -> None:
        kind: str | None = None
        for idx, line in enumerate(source.text.splitlines(), start=1):
            kind_match = re.match(r"^\s*kind:\s*([A-Za-z][A-Za-z0-9_-]*)\s*$", line)
            if kind_match:
                kind = kind_match.group(1)
                continue
            if not kind:
                continue
            name_match = re.match(r"^\s*name:\s*(.+?)\s*$", line)
            if not name_match:
                continue
            name = name_match.group(1).strip().strip("'\"")
            result.runtime_bindings.append(RuntimeBinding(
                id=stable_id(source.repo_id, source.relative_path, kind, name, idx),
                repo_id=source.repo_id,
                file_id=source.file_id,
                kind=f"k8s_{kind.lower()}",
                name=name,
                target=None,
                details={"kind": kind, "metadata": {"name": name}, "templated": True, "line": idx},
                confidence=0.7,
            ))
            kind = None

    def _container_hint(self, doc: dict) -> str | None:
        s = str(doc)
        m = re.search(r"'image': '([^']+)'|\"image\": \"([^\"]+)\"", s)
        if m:
            return m.group(1) or m.group(2)
        return None
