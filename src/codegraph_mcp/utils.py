from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_id(*parts: object) -> str:
    return sha256_text(":".join(str(p) for p in parts))[:32]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_language(path: Path) -> str | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    parts = {part.lower() for part in path.parts}
    if name == "dockerfile" or name.endswith(".dockerfile"):
        return "dockerfile"
    if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return "compose"
    if (
        name in {"chart.yaml", "chart.yml", "chart.lock", "values.yaml", "values.yml"}
        or name.endswith((".yaml.gotmpl", ".yml.gotmpl", ".json.gotmpl"))
        or suffix == ".tpl"
        or ("charts" in parts and "templates" in parts and suffix in {".yaml", ".yml"})
    ):
        return "helm"
    if suffix in {".yaml", ".yml"} and parts.intersection({"k8s", "kubernetes", "manifests"}):
        return "kubernetes"
    mapping = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".go": "go",
        ".java": "java", ".cs": "csharp", ".rs": "rust",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
        ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".kts": "kotlin",
        ".scala": "scala", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
        ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".md": "markdown",
        ".tf": "terraform",
    }
    return mapping.get(suffix)


def is_probably_test(path: Path) -> bool:
    s = str(path).lower()
    return any(x in s for x in ["/test/", "/tests/", "\\test\\", "\\tests\\"]) or path.name.lower().startswith("test_") or path.name.lower().endswith(("_test.py", ".test.ts", ".spec.ts", ".test.js", ".spec.js", "test.go"))


def token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract a zip archive into *dest*, returning the effective repo root.

    Security:
    - Guards against zip-slip by verifying every extracted path resolves
      inside *dest*.
    - Does not execute any extracted content.

    If the archive contains a single top-level directory, that directory is
    returned as the repo root (common for GitHub-style archives).  Otherwise
    *dest* itself is returned.

    Raises:
        ValueError: if a member path would escape *dest* (zip-slip).
        zipfile.BadZipFile: if the file is not a valid zip archive.
    """
    zip_path = Path(zip_path)
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Collect top-level entries while validating paths.
        top_level: set[str] = set()
        for member in zf.infolist():
            # Normalise the member name to a relative POSIX path.
            member_rel = Path(member.filename)
            # Guard: reject absolute paths and parent-traversal components.
            if member_rel.is_absolute() or ".." in member_rel.parts:
                raise ValueError(
                    f"Unsafe path in zip archive: {member.filename!r}"
                )
            resolved = (dest / member_rel).resolve()
            if not str(resolved).startswith(str(dest) + "/") and resolved != dest:
                raise ValueError(
                    f"Zip-slip detected for member {member.filename!r}"
                )
            if member_rel.parts:
                top_level.add(member_rel.parts[0])
        zf.extractall(dest)

    # If archive has exactly one top-level directory, use it as the root.
    if len(top_level) == 1:
        candidate = dest / next(iter(top_level))
        if candidate.is_dir():
            return candidate
    return dest
