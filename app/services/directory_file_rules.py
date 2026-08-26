"""Shared safety rules for browser/native directory uploads."""

from __future__ import annotations

from pathlib import PurePosixPath

KNOWLEDGE_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "build",
        "dist",
        ".dart_tool",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
    }
)

AGENT_IGNORED_DIRECTORY_NAMES = frozenset(
    {".git", "node_modules", "dist", "build", "vendor", "__pycache__"}
)

KNOWLEDGE_SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.json",
    }
)

AGENT_SECRET_FILE_NAMES = frozenset(
    {".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials.json"}
)


class InvalidDirectoryPath(ValueError):
    """An uploaded relative path is unsafe or exceeds structural limits."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_relative_path(
    raw: str,
    *,
    max_depth: int | None = 32,
    max_length: int | None = 500,
) -> str:
    value = str(raw or "").replace("\\", "/").strip("/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise InvalidDirectoryPath("unsafe_path")
    if (max_depth is not None and len(path.parts) > max_depth) or (
        max_length is not None and len(value) > max_length
    ):
        raise InvalidDirectoryPath("path_too_long")
    return path.as_posix()


def directory_skip_reason(
    relative_path: str,
    *,
    ignored_directory_names: frozenset[str],
    secret_file_names: frozenset[str],
) -> str | None:
    path = PurePosixPath(relative_path)
    lower_parts = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    if lower_parts & ignored_directory_names:
        return "ignored_directory"
    if (
        name in secret_file_names
        or (name.startswith(".env.") and name != ".env.example")
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    ):
        return "possible_secret"
    return None
