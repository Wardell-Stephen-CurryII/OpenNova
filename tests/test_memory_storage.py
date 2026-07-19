"""Tests for the legacy-compatible persistent memory storage API."""

from datetime import datetime

import pytest

from opennova.memory.storage import MemoryStorage
from opennova.memory.types.feedback_memory import FeedbackMemory
from opennova.memory.types.project_memory import ProjectMemory
from opennova.memory.types.reference_memory import ReferenceMemory
from opennova.memory.types.user_memory import UserMemory


@pytest.mark.parametrize(
    ("memory", "directory", "expected_type"),
    [
        (UserMemory(id="user-1", content="Prefer concise answers"), "user", UserMemory),
        (
            FeedbackMemory(id="feedback-1", content="The fix worked", feedback_type="approval"),
            "feedback",
            FeedbackMemory,
        ),
        (
            ProjectMemory(id="project-1", content="Use uv", decision="Use uv for dependencies"),
            "project",
            ProjectMemory,
        ),
        (
            ReferenceMemory(id="reference-1", content="Python docs", url="https://python.org"),
            "reference",
            ReferenceMemory,
        ),
    ],
)
def test_memory_storage_round_trip_by_string_category(
    tmp_path, memory, directory, expected_type
):
    storage = MemoryStorage(str(tmp_path))

    storage.save(memory)

    assert (tmp_path / directory / f"{memory.id}.json").is_file()
    restored = storage.get(memory.id, directory)
    assert isinstance(restored, expected_type)
    assert restored.content == memory.content
    assert restored.category == directory
    assert isinstance(restored.created_at, datetime)


def test_memory_storage_searches_all_categories(tmp_path):
    storage = MemoryStorage(str(tmp_path))
    storage.save(UserMemory(id="user-1", content="Use concise Python examples"))
    storage.save(ProjectMemory(id="project-1", content="Prefer Python for tooling"))
    storage.save(ReferenceMemory(id="reference-1", content="Rust documentation"))

    matches = storage.search("python")

    assert {memory.id for memory in matches} == {"user-1", "project-1"}
