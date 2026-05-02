from pathlib import Path

from pydantic_settings import BaseSettings

_DEFAULT_DATA = Path.home() / ".codegraph"


class Settings(BaseSettings):
    db_path: Path = _DEFAULT_DATA / "codegraph.db"
    repo_root: Path = _DEFAULT_DATA / "repos"
    host: str = "0.0.0.0"
    port: int = 8811
    read_only: bool = True
    enable_docker_logs: bool = False
    max_file_bytes: int = 1_000_000
    log_level: str = "INFO"
    allow_external_repos: bool = True

    # ── Enrichment settings ───────────────────────────────────────────────────
    # backend: heuristic (default, no LLM) | anthropic | openai | vllm | ollama | custom_http
    enrichment_backend: str = "heuristic"
    enrichment_auto: bool = True         # auto-enrich after every index_repo (heuristic is zero-cost)
    enrichment_batch_size: int = 15
    enrichment_max_functions: int = 500  # per enrich call

    # LLM credentials (used when enrichment_backend != heuristic)
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_base_url: str = ""               # for openai/vllm/ollama/custom_http backends

    model_config = {
        "env_prefix": "CODEGRAPH_",
        "case_sensitive": False,
    }


settings = Settings()


def ensure_runtime_dirs(runtime_settings: Settings = settings) -> None:
    """Create local state directories used by the CLI/server on first run."""
    runtime_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_settings.repo_root.mkdir(parents=True, exist_ok=True)
