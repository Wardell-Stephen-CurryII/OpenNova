"""Argument parsing and placeholder substitution helpers for markdown skills."""

from __future__ import annotations

import re
import shlex


def parse_arguments(args: str) -> list[str]:
    """Parse raw args using shell-like quoting rules with a safe fallback."""
    if not args or not args.strip():
        return []

    try:
        return shlex.split(args, posix=True)
    except ValueError:
        return args.split()


def parse_argument_names(argument_names: str | list[str] | None) -> list[str]:
    """Normalize frontmatter argument names while rejecting numeric placeholders."""
    if not argument_names:
        return []

    if isinstance(argument_names, str):
        values = re.split(r"[\s,]+", argument_names.strip())
    else:
        values = [str(item).strip() for item in argument_names]

    return [value for value in values if value and not value.isdigit()]


def generate_progressive_argument_hint(
    argument_names: list[str],
    typed_args: list[str],
) -> str | None:
    """Return a compact hint for the remaining named args."""
    remaining = argument_names[len(typed_args) :]
    if not remaining:
        return None
    return " ".join(f"[{name}]" for name in remaining)


def substitute_arguments(
    content: str,
    args: str | None,
    append_if_no_placeholder: bool = True,
    argument_names: list[str] | None = None,
) -> str:
    """Apply Claude Code-style placeholders to skill content."""
    if args is None:
        return content

    parsed_args = parse_arguments(args)
    argument_names = argument_names or []
    original_content = content

    for index, name in enumerate(argument_names):
        content = re.sub(rf"\${re.escape(name)}(?![\[\w])", parsed_args[index] if index < len(parsed_args) else "", content)

    content = re.sub(
        r"\$ARGUMENTS\[(\d+)\]",
        lambda match: parsed_args[int(match.group(1))] if int(match.group(1)) < len(parsed_args) else "",
        content,
    )
    content = re.sub(
        r"\$(\d+)(?!\w)",
        lambda match: parsed_args[int(match.group(1))] if int(match.group(1)) < len(parsed_args) else "",
        content,
    )
    content = content.replace("$ARGUMENTS", args)
    content = content.replace("$ARGS", args)

    if content == original_content and append_if_no_placeholder and args:
        content = f"{content}\n\nARGUMENTS: {args}"

    return content
