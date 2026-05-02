from pathlib import Path


class PathJailError(ValueError):
    pass


def ensure_within(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    if root_resolved == path_resolved:
        return path_resolved
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PathJailError(f"Path {path_resolved} is outside allowed root {root_resolved}") from exc
    return path_resolved
