"""La política de directorios debe ser coherente entre tipos de importación."""

import pytest

from app.services.directory_file_rules import (
    AGENT_IGNORED_DIRECTORY_NAMES,
    AGENT_SECRET_FILE_NAMES,
    KNOWLEDGE_IGNORED_DIRECTORY_NAMES,
    KNOWLEDGE_SECRET_FILE_NAMES,
    InvalidDirectoryPath,
    directory_skip_reason,
    normalize_relative_path,
)


def test_agent_policy_extends_knowledge_policy() -> None:
    assert AGENT_IGNORED_DIRECTORY_NAMES == (
        KNOWLEDGE_IGNORED_DIRECTORY_NAMES | {"vendor"}
    )
    assert AGENT_SECRET_FILE_NAMES == (
        KNOWLEDGE_SECRET_FILE_NAMES | {".npmrc", ".pypirc"}
    )


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        (".vscode/settings.json", "ignored_directory"),
        ("vendor/package/agent.md", "ignored_directory"),
        ("config/.npmrc", "possible_secret"),
        ("private/secrets.json", "possible_secret"),
        ("agents/reviewer.md", None),
        (".github/agents/reviewer.md", None),
    ],
)
def test_agent_directory_skip_reason(path: str, reason: str | None) -> None:
    assert (
        directory_skip_reason(
            path,
            ignored_directory_names=AGENT_IGNORED_DIRECTORY_NAMES,
            secret_file_names=AGENT_SECRET_FILE_NAMES,
        )
        == reason
    )


def test_default_structural_limits_reject_deep_and_long_paths() -> None:
    with pytest.raises(InvalidDirectoryPath, match="path_too_long"):
        normalize_relative_path("/".join(["level"] * 33 + ["agent.md"]))
    with pytest.raises(InvalidDirectoryPath, match="path_too_long"):
        normalize_relative_path(f"agents/{'a' * 500}.md")
