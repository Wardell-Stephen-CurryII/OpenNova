# OpenNova Code Repair Skill Usage

## Overview

The `code_repair` skill transforms OpenNova from a general-purpose AI coding agent into a task-specific **intelligent model-driven code defect detection and repair framework**.

The skill encodes a 6-stage workflow:
1. **Static Analysis** — Read source code, identify suspicious patterns
2. **Test Feedback** — Execute pytest to collect failure evidence
3. **Error Pattern Classification** — Classify bug into one of 9 categories
4. **Repair Planning** — Explain root cause and propose minimal fix
5. **Patch Application** — Apply the fix to the target file
6. **Validation** — Re-run tests to verify the fix

## Installation

The skill is already included in the project at:

```
.opennova/skills/code_repair/SKILL.md
```

OpenNova auto-discovers skills from `.opennova/skills/` on startup.

## Usage

### Command Line

```bash
# Analyze and repair a single bug sample
uv run opennova run "/skill code_repair repair_bench/datasets/division_by_zero_001"

# In REPL mode
uv run opennova
> /skill code_repair repair_bench/datasets/index_out_of_range_001
```

### Batch Experiment

```bash
# Run on all samples (uses live API)
python repair_bench/scripts/run_opennova_repair.py

# Run on a specific sample
python repair_bench/scripts/run_opennova_repair.py --dataset division_by_zero_001

# Mock mode for CI (no API calls)
python repair_bench/scripts/run_opennova_repair.py --mock
```

## Skill Configuration

| Field | Value |
|---|---|
| Name | `code_repair` |
| Arguments | `<bug-sample-directory>` |
| Allowed Tools | `read_file`, `list_directory`, `execute_command`, `write_file` |
| User Invocable | Yes |

## Output Format

The skill produces a structured JSON report (`repair_result.json`) in the target directory:

```json
{
  "sample": "division_by_zero_001",
  "bug_detected": true,
  "bug_type": "division_by_zero",
  "location": {"file": "buggy.py", "line": 2, "function": "divide"},
  "static_analysis": {...},
  "test_feedback": {...},
  "classification": {...},
  "repair": {...},
  "validation": {...}
}
```

## Supported Bug Categories

| Category | Description |
|---|---|
| `division_by_zero` | Division or modulo by zero without guard |
| `index_out_of_range` | List/array/string index access beyond bounds |
| `none_dereference` | Attribute access or method call on None |
| `type_mismatch` | Operation on incompatible types |
| `missing_return` | Function missing return statement on some code path |
| `mutable_default_argument` | Mutable object used as default argument |
| `off_by_one` | Loop boundary off by one |
| `logic_error` | Incorrect conditional, wrong operator, wrong algorithm |
| `resource_leak` | File/socket/connection not closed properly |
| `unknown` | Does not fit any category above |
