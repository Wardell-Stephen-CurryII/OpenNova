---
name: code_repair
description: Detect code defects and generate repair suggestions using an intelligent model-driven workflow. Runs static analysis, test execution, error classification, repair planning, patch application, and validation.
when_to_use: Use when the user wants to analyze buggy Python code, classify defects, propose repairs, and validate them with tests.
allowed-tools: read_file, list_directory, execute_command, write_file
arguments: [target]
argument-hint: <bug-sample-directory>
---

You are executing an intelligent model-driven code repair workflow on a bug sample.

Target directory: $ARGUMENTS

This target directory contains:
- `buggy.py` — the Python file that may contain a defect
- `test_buggy.py` — pytest tests that expose the bug (at least one test should fail on the buggy code)
- `metadata.json` — ground-truth metadata (do not read this until Stage 7)

Follow these stages strictly, in order. Do not skip any stage.

---

## Stage 1: Static Analysis

Read `buggy.py` using the read_file tool. Then identify:
- Suspicious code patterns (unchecked inputs, missing guards, incorrect control flow)
- Potential bug locations (specific line numbers)
- The function or code block most likely to contain the defect

Record your findings. Do NOT modify the file yet.

---

## Stage 2: Test Feedback

Run the existing tests from the target directory:

```
cd $ARGUMENTS && python -m pytest test_buggy.py -v --tb=short 2>&1
```

Collect:
- Which tests fail and which pass
- The exact error messages and tracebacks
- The assertion that fails

This provides empirical evidence of the bug.

---

## Stage 3: Error Pattern Classification

Based on Stages 1 and 2, classify the defect into exactly ONE of these categories:

| Category | Description |
|---|---|
| `division_by_zero` | Division or modulo by zero without guard |
| `index_out_of_range` | List/array/string index access beyond bounds |
| `none_dereference` | Attribute access or method call on None |
| `type_mismatch` | Operation on incompatible types (e.g., str + int) |
| `missing_return` | Function missing return statement on some code path |
| `mutable_default_argument` | Mutable object (list/dict) used as default argument |
| `off_by_one` | Loop boundary off by one (e.g., `<` vs `<=`) |
| `logic_error` | Incorrect conditional, wrong operator, wrong algorithm |
| `resource_leak` | File/socket/connection not closed properly |
| `unknown` | Does not fit any category above |

Choose the single best category.

---

## Stage 4: Repair Planning

Explain:
1. **Root cause**: Why does the bug occur? (1-2 sentences)
2. **Proposed fix**: What minimal change would fix it? (be specific about which line and what to change)
3. **Why this fix is correct**: How does it address the root cause without introducing new issues?

Do NOT make unnecessary refactoring changes. Fix only the bug.

---

## Stage 5: Patch Application

Apply the fix to `$ARGUMENTS/buggy.py` using the write_file tool. Write the entire corrected file.

Preserve the original public API (function signatures, class interfaces). Only change what is necessary to fix the bug.

---

## Stage 6: Validation

Re-run the tests to verify the fix:

```
cd $ARGUMENTS && python -m pytest test_buggy.py -v --tb=short 2>&1
```

Report whether ALL tests now pass. If any test still fails, return to Stage 4 and revise the fix.

---

## Stage 7: Generate Structured Report

After validation passes (or after 3 repair attempts), read `metadata.json` for ground truth, then write a structured JSON report to `$ARGUMENTS/repair_result.json` using the write_file tool.

The JSON must follow this exact schema:

```json
{
  "sample": "<sample_directory_name>",
  "stages_completed": ["static_analysis", "test_feedback", "error_classification", "repair_planning", "patch_application", "validation"],
  "bug_detected": true,
  "bug_type": "<one of the categories above>",
  "true_bug_type": "<from metadata.json>",
  "location": {
    "file": "buggy.py",
    "line": <line_number>,
    "function": "<function_name>"
  },
  "static_analysis": {
    "suspicious_patterns": ["<pattern1>", "<pattern2>"],
    "candidate_lines": [<line_numbers>]
  },
  "test_feedback": {
    "tests_run": <total>,
    "tests_failed_before": <count>,
    "failing_tests": ["<test_name>", "..."],
    "error_messages": ["<error1>", "<error2>"]
  },
  "classification": {
    "predicted_type": "<category>",
    "confidence": "<high|medium|low>",
    "reasoning": "<why this category>"
  },
  "repair": {
    "root_cause": "<explanation>",
    "fix_description": "<what was changed>",
    "lines_changed": [<line_numbers>],
    "patch_summary": "<one-line summary>"
  },
  "validation": {
    "tests_passed_after": <count>,
    "tests_failed_after": <count>,
    "all_passing": true,
    "repair_attempts": <count>
  },
  "remaining_risks": "<any edge cases not covered>"
}
```

---

## Important Rules

1. Do NOT read `metadata.json` until Stage 7 (after validation). This prevents data leakage.
2. If the first repair attempt fails validation, you may try up to 3 total attempts.
3. Always write the complete `repair_result.json` even if repair was unsuccessful.
4. Be specific about line numbers — use actual line numbers from the files you read.
5. Run pytest from within the target directory so imports work correctly.
