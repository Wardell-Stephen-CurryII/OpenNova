---
name: code_review
description: Review code changes for correctness, maintainability, security, and clarity.
when_to_use: Use when the user asks for a code review, wants feedback on a diff, or needs a second opinion on code quality.
allowed-tools: read_file, list_directory
arguments: [target]
argument-hint: <file-or-diff-target>
---
Review the target code or diff carefully.

Target: $ARGUMENTS

Focus on:
- correctness and obvious bugs
- security issues
- readability and maintainability
- missing edge cases or tests

Provide concrete findings first. If there are no meaningful issues, say that clearly.
