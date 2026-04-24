"""
Detect structural Markdown that Signal won't render.

The goal is in-context training: when a tool call contains obvious Markdown
syntax, return an error so the agent rewrites and retries. We do NOT modify
message content — that would destroy intent (URLs with underscores, emphasis
the author meant, etc.). We only flag unambiguous structural formatting.

Inline emphasis (*bold*, _italic_) is not flagged. Signal Desktop renders some
inline emphasis, and stray asterisks in prose are ambiguous.
"""

import re

_HEADING = re.compile(r"^#{1,6} \S", re.MULTILINE)
_FENCE = re.compile(r"```")
_BULLET_LINE = re.compile(r"^[-*+] \S")
_LINK_SYNTAX = re.compile(r"\[[^\]\n]+\]\([^)\s]+\)")


def _has_bullet_list(text: str) -> bool:
    run = 0
    for line in text.splitlines():
        if _BULLET_LINE.match(line):
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
    return False


def detect_structural_markdown(text: str) -> str | None:
    """
    Return an error message if `text` contains structural Markdown, else None.

    Flags headings, code fences, multi-line bullet lists, and link syntax.
    Does not flag inline emphasis or single stray markers.
    """
    problems: list[str] = []
    if _HEADING.search(text):
        problems.append("Markdown headings (# ...)")
    if _FENCE.search(text):
        problems.append("code fences (```)")
    if _has_bullet_list(text):
        problems.append("bulleted list (-/+/*)")
    if _LINK_SYNTAX.search(text):
        problems.append("link syntax ([text](url))")

    if not problems:
        return None
    return (
        "Signal renders plain text; this message contains "
        + ", ".join(problems)
        + ". Rewrite as prose paragraphs — bare URLs, no headings/fences/lists — and resend."
    )
