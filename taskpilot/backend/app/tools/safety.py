import re
from pathlib import Path

from app.enums import ErrorType
from app.tools.base import ToolFailure

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}


def resolve_safe(root: Path, rel: str, *, must_exist: bool = True) -> Path:
    """Resolve a user/LLM-supplied relative path inside the workspace root.

    Blocks absolute paths, drive letters, NUL bytes, '..' escapes and symlink
    escapes (the resolved path must stay under the resolved root).
    """
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise ToolFailure(ErrorType.VALIDATION, "path must be a non-empty string")
    if Path(rel).is_absolute() or re.match(r"^[A-Za-z]:", rel) or rel.startswith(("/", "\\")):
        raise ToolFailure(ErrorType.PERMISSION, "absolute paths are not allowed; use workspace-relative paths")
    root_resolved = root.resolve()
    full = (root_resolved / rel).resolve()
    if not full.is_relative_to(root_resolved):
        raise ToolFailure(ErrorType.PERMISSION, "path escapes the allowed workspace")
    if must_exist and not full.exists():
        raise ToolFailure(ErrorType.NOT_FOUND, f"file not found: {rel}")
    return full


def rel_posix(root: Path, full: Path) -> str:
    return full.resolve().relative_to(root.resolve()).as_posix()
