"""
Diff/Patch system for safe code modifications.

This module provides:
- DiffEngine: Generate and apply unified diffs
- DiffParser: Parse LLM output file changes
- ChangeSet: Track multiple file changes
"""

from opennova.diff.engine import DiffEngine, ApplyResult, Hunk
from opennova.diff.parser import DiffParser, FileChange, ChangeType
from opennova.diff.changeset import ChangeSet, ChangeResult

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
