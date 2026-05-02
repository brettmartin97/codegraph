from codegraph_mcp.analyzers.base import LanguageAnalyzer
from codegraph_mcp.analyzers.config import ConfigAnalyzer, DockerfileAnalyzer
from codegraph_mcp.analyzers.generic import PythonAnalyzer, RegexFunctionAnalyzer
from codegraph_mcp.analyzers.runtime import RuntimeAnalyzer
from codegraph_mcp.analyzers.treesitter import (
    TREE_SITTER_AVAILABLE,
    TREE_SITTER_JVM_AVAILABLE,
    TreeSitterCSharpAnalyzer,
    TreeSitterGoAnalyzer,
    TreeSitterJavaAnalyzer,
    TreeSitterJSAnalyzer,
    TreeSitterRustAnalyzer,
)


class AnalyzerRegistry:
    def __init__(self) -> None:
        ts_base: list[LanguageAnalyzer] = (
            [TreeSitterJSAnalyzer(), TreeSitterGoAnalyzer()]
            if TREE_SITTER_AVAILABLE else []
        )
        ts_jvm: list[LanguageAnalyzer] = (
            [TreeSitterJavaAnalyzer(), TreeSitterRustAnalyzer(), TreeSitterCSharpAnalyzer()]
            if TREE_SITTER_JVM_AVAILABLE else []
        )
        self.analyzers: list[LanguageAnalyzer] = (
            [PythonAnalyzer()]
            + ts_base
            + ts_jvm
            + [RegexFunctionAnalyzer(), RuntimeAnalyzer(), ConfigAnalyzer(), DockerfileAnalyzer()]
        )

    def get(self, language: str, path):
        analyzers = self.get_all(language, path)
        if analyzers:
            return analyzers[0]
        return None

    def get_all(self, language: str, path):
        matches: list[LanguageAnalyzer] = []
        for analyzer in self.analyzers:
            if analyzer.supports(language, path):
                matches.append(analyzer)

        if not matches:
            return []

        config_languages = {"compose", "dockerfile", "helm", "json", "kubernetes", "terraform", "yaml"}
        if language in config_languages:
            return matches

        # Keep programming-language dispatch compatible with the historical
        # single-analyzer behavior: prefer the highest-confidence analyzer.
        return matches[:1]

    @property
    def active_languages(self) -> list[str]:
        langs = ["python", "yaml", "json", "kubernetes", "helm", "dockerfile"]
        if TREE_SITTER_AVAILABLE:
            langs += ["javascript", "typescript", "tsx", "go"]
        if TREE_SITTER_JVM_AVAILABLE:
            langs += ["java", "rust", "csharp"]
        return langs
