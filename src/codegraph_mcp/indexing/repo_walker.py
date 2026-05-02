import os
from pathlib import Path

from codegraph_mcp.utils import detect_language

DEFAULT_EXCLUDES = {
    ".git", "node_modules", ".venv", "venv", "env", "dist", "build",
    "target", "vendor", "coverage", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "eggs", ".eggs",
    "htmlcov", ".hypothesis",
}

GENERATED_HINTS = (
    "generated", ".min.js", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "composer.lock",
)

# Files that must never be indexed — secrets, credentials, keys
SECRET_FILENAME_EXACT = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging",
    ".env.development", ".env.test", ".env.example",
    "secrets.yaml", "secrets.yml", "secrets.json",
    "credentials", "credentials.json", "credentials.yaml",
    "service-account.json", "serviceaccount.json",
    ".netrc", ".pgpass", ".my.cnf", ".boto",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "id_rsa.pub", "id_ed25519.pub",
})

SECRET_SUFFIX = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore",
    ".pkcs12", ".cer", ".crt", ".der",
})

SECRET_PATTERN_PREFIXES = (
    ".env.", "secret", "credential", "password", "token", "apikey", "api_key",
)


def _is_secret_file(path: Path) -> bool:
    name = path.name.lower()
    if name in SECRET_FILENAME_EXACT:
        return True
    if path.suffix.lower() in SECRET_SUFFIX:
        return True
    if any(name.startswith(p) for p in SECRET_PATTERN_PREFIXES):
        return True
    return False


class RepoWalker:
    def __init__(self, max_file_bytes: int = 1_000_000):
        self.max_file_bytes = max_file_bytes

    def iter_files(self, repo_path: Path):
        """Walk repo_path pruning excluded dirs at the directory level (fast)."""
        for dirpath, dirnames, filenames in os.walk(repo_path):
            dirnames[:] = [
                d for d in dirnames
                if d not in DEFAULT_EXCLUDES and not d.startswith(".")
            ]
            dirpath_obj = Path(dirpath)
            for filename in filenames:
                if any(h in filename.lower() for h in GENERATED_HINTS):
                    continue
                path = dirpath_obj / filename
                # Never index secret/credential files
                if _is_secret_file(path):
                    continue
                if not detect_language(path):
                    continue
                try:
                    if path.stat().st_size > self.max_file_bytes:
                        continue
                except OSError:
                    continue
                yield path
