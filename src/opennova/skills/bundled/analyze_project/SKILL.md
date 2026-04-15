---
name: analyze_project
description: Analyze a project structure, architecture, and likely extension points.
when_to_use: Use when the user asks for an overview of a repository, architecture walkthrough, or help locating relevant code.
allowed-tools: read_file, list_directory
arguments: [target]
argument-hint: <project-path-or-area>
---
Analyze the requested project or directory.

Target: $ARGUMENTS

Summarize:
- main subsystems
- important files/directories
- likely extension points
- risks or unknowns that need deeper reading
