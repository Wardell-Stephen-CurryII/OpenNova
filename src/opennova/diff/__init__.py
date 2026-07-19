"""
Diff/Patch system for safe code modifications.

This module provides:
- DiffEngine: Generate and apply unified diffs
- DiffParser: Parse LLM output file changes
- ChangeSet: Track multiple file changes
"""

from opennova.diff.changeset import ChangeResult, ChangeSet
from opennova.diff.engine import ApplyResult, DiffEngine, Hunk
from opennova.diff.parser import ChangeType, DiffParser, FileChange

__all__ = [
    "DiffEngine",
    "ApplyResult",
    "Hunk",
    "DiffParser",
    "FileChange",
    "ChangeType",
    "ChangeSet",
    "ChangeResult",
]
