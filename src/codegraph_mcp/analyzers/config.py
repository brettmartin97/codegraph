from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from codegraph_mcp.analyzers.base import AnalysisResult, LanguageAnalyzer, SourceFile
from codegraph_mcp.graph.models import EdgeType, FunctionDescriptor, FunctionEdge, FunctionNode
from codegraph_mcp.utils import sha256_text, stable_id


class ConfigAnalyzer(LanguageAnalyzer):
    """Parses JSON and YAML configurations, including Kubernetes and Helm (best effort)."""

    language = "yaml"
    extensions = {".yaml", ".yml", ".json", ".yaml.gotmpl", ".tpl"}

    def supports(self, language: str, path: Path) -> bool:
        ext = path.suffix.lower()
        name = path.name.lower()
        return (
            language in ("compose", "yaml", "json", "helm", "kubernetes")
            or ext in self.extensions
            or name.endswith((".yaml.gotmpl", ".yml.gotmpl", ".json.gotmpl"))
        )

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        path = Path(source.relative_path)
        ext = path.suffix.lower()
        is_json = ext == ".json" and source.language != "helm"

        try:
            if is_json:
                data = json.loads(source.text)
                self._extract_json(data, source, result)
            else:
                # Handle multi-document YAML (common in k8s)
                # Ignore errors for Helm go-templates (best effort)
                try:
                    docs = list(yaml.safe_load_all(source.text))
                    for doc_idx, doc in enumerate(docs):
                        if doc:
                            self._extract_yaml(doc, source, result, doc_idx)
                except Exception:
                    # Fallback for templated helm charts that fail standard yaml parse
                    self._extract_regex(source, result)
        except Exception:
            # Fallback
            self._extract_regex(source, result)

        return result

    def _extract_json(self, data: Any, source: SourceFile, result: AnalysisResult) -> None:
        if isinstance(data, dict):
            if self._extract_package_json(data, source, result):
                return

            keys = list(data.keys())
            summary = f"JSON config object with {len(keys)} keys."

            # Identify if it's package.json
            name = data.get("name", source.relative_path.split("/")[-1].split("\\")[-1])
            if "dependencies" in data:
                summary = f"Node.js package config for {name}."

            self._create_resource_node(name, summary, source, result, data, kind="JsonConfig")
        elif isinstance(data, list):
            self._create_resource_node("json_array", f"JSON array with {len(data)} items", source, result, data, kind="JsonConfig")

    def _extract_yaml(self, doc: Any, source: SourceFile, result: AnalysisResult, doc_idx: int = 0) -> None:
        if isinstance(doc, dict):
            # Check for Kubernetes
            api_version = doc.get("apiVersion")
            kind = doc.get("kind")
            metadata = doc.get("metadata", {})
            name = metadata.get("name") if isinstance(metadata, dict) else None

            if api_version and kind:
                name = name or kind.lower()
                summary = f"Kubernetes {kind} resource ({api_version})"
                self._create_resource_node(name, summary, source, result, doc, kind=kind, identity=doc_idx)
                return

            if source.language == "helm":
                file_name = Path(source.relative_path).name.lower()
                if file_name in {"chart.yaml", "chart.yml"}:
                    name = str(doc.get("name") or "chart")
                    summary = f"Helm chart {name}"
                    self._create_resource_node(name, summary, source, result, doc, kind="HelmChart", identity=doc_idx)
                    return
                if file_name in {"values.yaml", "values.yml"}:
                    name = Path(source.relative_path).parent.name or "values"
                    summary = f"Helm values for {name}"
                    self._create_resource_node(name, summary, source, result, doc, kind="HelmValues", identity=doc_idx)
                return

            keys = list(doc.keys())
            name = source.relative_path.split("/")[-1].split("\\")[-1]
            summary = f"YAML config object with keys: {', '.join(keys[:5])}"
            self._create_resource_node(name, summary, source, result, doc, identity=doc_idx)

    def _extract_package_json(self, data: dict[str, Any], source: SourceFile, result: AnalysisResult) -> bool:
        path = Path(source.relative_path)
        is_package = path.name.lower() == "package.json" or "scripts" in data or "dependencies" in data
        if not is_package:
            return False

        package_name = str(data.get("name") or path.parent.name or "package")
        package = self._create_resource_node(
            package_name,
            f"Node.js package {package_name}",
            source,
            result,
            data,
            kind="PackageJson",
            identity="package",
            start_line=self._find_json_key_line(source.text, "name") or 1,
        )

        scripts = data.get("scripts")
        if isinstance(scripts, dict):
            for script_name, command in scripts.items():
                script = self._create_resource_node(
                    str(script_name),
                    f"npm script '{script_name}': {command}",
                    source,
                    result,
                    {"script": script_name, "command": command},
                    kind="NpmScript",
                    identity=("script", script_name),
                    start_line=self._find_json_key_line(source.text, str(script_name)) or package.start_line,
                    node_kind="json_script",
                )
                self._add_direct_edge(package, script, result, "package_script", script.start_line)

        dependency_sections = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
        for section in dependency_sections:
            deps = data.get(section)
            if not isinstance(deps, dict):
                continue
            for dep_name, version in deps.items():
                dep = self._create_resource_node(
                    str(dep_name),
                    f"{section} dependency {dep_name}@{version}",
                    source,
                    result,
                    {"name": dep_name, "version": version, "section": section},
                    kind="NpmDependency",
                    identity=("dependency", section, dep_name),
                    start_line=self._find_json_key_line(source.text, str(dep_name)) or package.start_line,
                    node_kind="json_dependency",
                )
                self._add_direct_edge(package, dep, result, "package_dependency", dep.start_line)

        return True

    def _create_resource_node(self, name: str, summary: str, source: SourceFile,
                              result: AnalysisResult, data: Any, kind: str | None = None,
                              identity: object | None = None,
                              start_line: int = 1, end_line: int | None = None,
                              node_kind: str = "config_resource") -> FunctionNode:
        body = json.dumps(data, default=str)[:1000] if isinstance(data, (dict, list)) else str(data)[:1000]
        display_kind = kind or "Config"
        fid = stable_id(source.repo_id, source.relative_path, display_kind, name, identity if identity is not None else sha256_text(body)[:12])

        desc = FunctionDescriptor(
            function_id=fid,
            summary=summary,
            source="config_analyzer",
            category="config",
            purpose=summary,
            quality_score=0.8
        )

        lines = source.text.count("\n") + 1
        end_line = end_line or start_line

        fn = FunctionNode(
            id=fid,
            repo_id=source.repo_id,
            file_id=source.file_id,
            language=source.language or "yaml",
            kind=node_kind,
            name=name,
            qualified_name=f"{display_kind}.{name}",
            display_name=f"{display_kind} {name}",
            start_line=start_line,
            end_line=min(end_line, lines),
            signature=f"{display_kind}: {name}",
            descriptor=desc,
            body_hash=sha256_text(body),
            signature_hash=sha256_text(name),
            descriptor_hash=sha256_text(summary),
            loc=max(1, min(end_line, lines) - start_line + 1),
            confidence=0.9
        )
        result.functions.append(fn)
        return fn

    def _add_direct_edge(
        self,
        source_node: FunctionNode,
        target_node: FunctionNode,
        result: AnalysisResult,
        relationship: str,
        line: int,
    ) -> None:
        result.edges.append(FunctionEdge(
            id=stable_id(source_node.repo_id, source_node.id, "CALLS", target_node.id, relationship, line),
            repo_id=source_node.repo_id,
            source_function_id=source_node.id,
            target_function_id=target_node.id,
            edge_type=EdgeType.calls,
            confidence=0.9,
            evidence={"line": line, "relationship": relationship, "extractor": "config_analyzer"},
        ))

    def _find_json_key_line(self, text: str, key: str) -> int | None:
        pattern = re.compile(rf'^\s*"{re.escape(key)}"\s*:', re.MULTILINE)
        match = pattern.search(text)
        if not match:
            return None
        return text.count("\n", 0, match.start()) + 1

    def _extract_regex(self, source: SourceFile, result: AnalysisResult) -> None:
        """Fallback for un-parseable templates (e.g. Helm)."""
        import re

        lines = source.text.splitlines()
        current_kind = None

        for i, line in enumerate(lines):
            # Best effort k8s/helm regex
            kind_match = re.match(r"^\s*kind:\s*([A-Za-z][A-Za-z0-9_-]*)\s*$", line)
            if kind_match:
                current_kind = kind_match.group(1)

            name_match = re.match(r"^\s*name:\s*(.+?)\s*$", line)
            if name_match and current_kind:
                current_name = name_match.group(1).strip().strip("'\"")
                self._create_resource_node(
                    name=current_name,
                    summary=f"Templated Kubernetes/Helm {current_kind} resource",
                    source=source,
                    result=result,
                    data={"kind": current_kind, "name": current_name},
                    kind=current_kind,
                    identity=i + 1,
                )
                current_kind = None


class DockerfileAnalyzer(LanguageAnalyzer):
    """Parses Dockerfiles into stages/steps."""

    language = "dockerfile"
    extensions = {"Dockerfile", ".dockerfile"}
    _ENV_REF_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
    _BUILDER_ENV_INSTRUCTIONS = {
        "ADD",
        "COPY",
        "ENV",
        "EXPOSE",
        "FROM",
        "LABEL",
        "ONBUILD",
        "STOPSIGNAL",
        "USER",
        "VOLUME",
        "WORKDIR",
    }
    _SHELL_ENV_INSTRUCTIONS = {"RUN", "CMD", "ENTRYPOINT", "HEALTHCHECK"}

    def supports(self, language: str, path: Path) -> bool:
        name = path.name.lower()
        return language == "dockerfile" or name == "dockerfile" or name.endswith(".dockerfile")

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        lines = source.text.splitlines()

        from_pattern = re.compile(r"^FROM\s+([^\s]+)(?:\s+AS\s+([^\s]+))?", re.I)
        instruction_pattern = re.compile(r"^([A-Z]+)\b(.*)$", re.I)
        stages: list[dict[str, Any]] = []
        env_by_name: dict[str, FunctionNode] = {}

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            m = from_pattern.match(line.strip())
            if m:
                base_image = m.group(1)
                stages.append({
                    "base_image": base_image,
                    "name": m.group(2) if m.group(2) else f"stage_{i}",
                    "start_line": i,
                    "raw": line,
                })
                continue

            instruction_match = instruction_pattern.match(stripped)
            if not instruction_match:
                continue
            instruction = instruction_match.group(1).upper()
            rest = instruction_match.group(2).strip()

            if instruction == "ENV":
                for name, value in self._parse_env_assignments(rest):
                    env_node = self._create_docker_node(
                        source=source,
                        result=result,
                        name=name,
                        qualified_name=f"DockerEnv.{name}",
                        display_name=f"ENV {name}",
                        kind="docker_env",
                        start_line=i,
                        end_line=i,
                        signature=f"ENV {name}={value}",
                        summary=f"Docker environment variable {name}",
                        body=value,
                    )
                    for ref_name in self._env_refs(value):
                        ref_node = env_by_name.get(ref_name)
                        if ref_node:
                            self._add_direct_edge(ref_node, env_node, result, "docker_env_reference", i)
                    env_by_name[name] = env_node
                continue

            refs = self._instruction_env_refs(instruction, rest)
            if not refs:
                continue
            instruction_node = self._create_docker_node(
                source=source,
                result=result,
                name=f"{instruction}:{i}",
                qualified_name=f"DockerInstruction.{instruction}:{i}",
                display_name=f"{instruction} line {i}",
                kind="docker_instruction",
                start_line=i,
                end_line=i,
                signature=stripped,
                summary=f"Docker {instruction} instruction using environment variables",
                body=stripped,
            )
            for ref_name in refs:
                env_node = env_by_name.get(ref_name)
                if env_node:
                    self._add_direct_edge(env_node, instruction_node, result, "docker_env_consumer", i)

        for idx, stage in enumerate(stages):
            end_line = (stages[idx + 1]["start_line"] - 1) if idx + 1 < len(stages) else max(1, len(lines))
            result.functions.append(self._create_stage_node(
                source=source,
                name=stage["name"],
                base_image=stage["base_image"],
                start_line=stage["start_line"],
                end_line=end_line,
                body="\n".join(lines[stage["start_line"] - 1:end_line]),
                raw=stage["raw"],
            ))

        # Add one for the entire file if no FROM was found
        if not result.functions:
            self._fallback_docker(source, result)

        return result

    def _parse_env_assignments(self, rest: str) -> list[tuple[str, str]]:
        try:
            parts = shlex.split(rest, posix=True)
        except ValueError:
            parts = rest.split()
        if not parts:
            return []
        if len(parts) >= 2 and "=" not in parts[0]:
            return [(parts[0], " ".join(parts[1:]))]
        assignments: list[tuple[str, str]] = []
        for part in parts:
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                assignments.append((name, value))
        return assignments

    def _env_refs(self, text: str) -> set[str]:
        return {match.group(1) or match.group(2) for match in self._ENV_REF_RE.finditer(text)}

    def _instruction_env_refs(self, instruction: str, rest: str) -> set[str]:
        if instruction in self._BUILDER_ENV_INSTRUCTIONS:
            return self._env_refs(rest)
        if instruction in self._SHELL_ENV_INSTRUCTIONS and not rest.lstrip().startswith("["):
            return self._env_refs(rest)
        return set()

    def _create_stage_node(
        self,
        source: SourceFile,
        name: str,
        base_image: str,
        start_line: int,
        end_line: int,
        body: str,
        raw: str,
    ) -> FunctionNode:
        fid = stable_id(source.repo_id, source.relative_path, name, base_image)
        summary = f"Docker build stage '{name}' from {base_image}"

        desc = FunctionDescriptor(
            function_id=fid,
            summary=summary,
            source="dockerfile_analyzer",
            category="config",
            purpose=summary,
            quality_score=0.8
        )

        return FunctionNode(
            id=fid,
            repo_id=source.repo_id,
            file_id=source.file_id,
            language="dockerfile",
            kind="docker_stage",
            name=name,
            qualified_name=f"DockerStage.{name}",
            display_name=f"Stage: {name}",
            start_line=start_line,
            end_line=end_line,
            signature=f"FROM {base_image} AS {name}",
            descriptor=desc,
            body_hash=sha256_text(body or raw),
            signature_hash=sha256_text(base_image),
            descriptor_hash=sha256_text(summary),
            loc=max(1, end_line - start_line + 1),
            confidence=0.9
        )

    def _create_docker_node(
        self,
        source: SourceFile,
        result: AnalysisResult,
        name: str,
        qualified_name: str,
        display_name: str,
        kind: str,
        start_line: int,
        end_line: int,
        signature: str,
        summary: str,
        body: str,
    ) -> FunctionNode:
        fid = stable_id(source.repo_id, source.relative_path, qualified_name, start_line)
        desc = FunctionDescriptor(
            function_id=fid,
            summary=summary,
            source="dockerfile_analyzer",
            category="config",
            purpose=summary,
            quality_score=0.82,
        )
        fn = FunctionNode(
            id=fid,
            repo_id=source.repo_id,
            file_id=source.file_id,
            language="dockerfile",
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            display_name=display_name,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            descriptor=desc,
            body_hash=sha256_text(body),
            signature_hash=sha256_text(signature),
            descriptor_hash=sha256_text(summary),
            loc=max(1, end_line - start_line + 1),
            confidence=0.88,
        )
        result.functions.append(fn)
        return fn

    def _add_direct_edge(
        self,
        source_node: FunctionNode,
        target_node: FunctionNode,
        result: AnalysisResult,
        relationship: str,
        line: int,
    ) -> None:
        result.edges.append(FunctionEdge(
            id=stable_id(source_node.repo_id, source_node.id, "CALLS", target_node.id, relationship, line),
            repo_id=source_node.repo_id,
            source_function_id=source_node.id,
            target_function_id=target_node.id,
            edge_type=EdgeType.calls,
            confidence=0.88,
            evidence={"line": line, "relationship": relationship, "extractor": "dockerfile_analyzer"},
        ))

    def _fallback_docker(self, source: SourceFile, result: AnalysisResult) -> None:
        name = source.relative_path.split("/")[-1].split("\\")[-1]
        fid = stable_id(source.repo_id, source.relative_path, "docker", name)
        desc = FunctionDescriptor(
            function_id=fid,
            summary="Docker build definition",
            source="dockerfile_analyzer",
            category="config",
            purpose="Docker build definition",
            quality_score=0.6
        )
        fn = FunctionNode(
            id=fid,
            repo_id=source.repo_id,
            file_id=source.file_id,
            language="dockerfile",
            kind="dockerfile",
            name=name,
            qualified_name=name,
            display_name=name,
            start_line=1,
            end_line=source.text.count("\n") + 1,
            signature="Dockerfile",
            descriptor=desc,
            body_hash=sha256_text(source.text[:1000]),
            signature_hash=sha256_text(name),
            descriptor_hash=sha256_text("Docker build definition"),
            loc=source.text.count("\n") + 1,
            confidence=0.8
        )
        result.functions.append(fn)
