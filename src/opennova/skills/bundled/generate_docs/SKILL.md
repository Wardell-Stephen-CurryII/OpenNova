---
name: generate_docs
description: Generate or improve documentation for code, modules, or APIs.
when_to_use: Use when the user asks for docstrings, README text, API docs, or documentation cleanup.
allowed-tools: read_file, write_file
arguments: [target]
argument-hint: <file-or-symbol>
---
Generate documentation for the requested target.

Target: $ARGUMENTS

Prefer concise, accurate documentation that matches the existing code.
Do not invent behavior that is not present in the source.
