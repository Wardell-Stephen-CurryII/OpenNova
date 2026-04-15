---
name: git_helper
description: Help with Git workflows, command selection, and repository status interpretation.
when_to_use: Use when the user asks how to perform a Git task, interpret Git status, or choose the right Git command.
allowed-tools: execute_command
arguments: [task]
argument-hint: <git-task>
---
Help with the following Git task:

$ARGUMENTS

Explain the safest command sequence for the task, note important cautions, and keep the guidance practical.
