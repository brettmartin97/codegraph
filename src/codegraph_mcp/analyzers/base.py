from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codegraph_mcp.graph.models import ClassNode, FunctionEdge, FunctionNode, RuntimeBinding


class SourceFile(BaseModel):
    repo_id: str
    file_id: str
    path: Path
    relative_path: str
    language: str
    text: str


class AnalysisResult(BaseModel):
    functions: list[FunctionNode] = Field(default_factory=list)
    classes: list[ClassNode] = Field(default_factory=list)
    edges: list[FunctionEdge] = Field(default_factory=list)
    runtime_bindings: list[RuntimeBinding] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class LanguageAnalyzer:
    language: str = "unknown"
    extensions: set[str] = set()

    def supports(self, language: str, path: Path) -> bool:
        return language == self.language

    def analyze(self, source: SourceFile) -> AnalysisResult:
        return AnalysisResult()
