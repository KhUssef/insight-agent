"""Read-only SQL validation.

This module is a security boundary. Model-supplied SQL is parsed and rejected
unless it is a single SELECT (or WITH ... SELECT) statement, with no write,
DDL, or database-management keywords anywhere in it. Enforcement never relies
on DuckDB permissions or on the model behaving.

The check is deliberately fail-closed: comments and string literals are
stripped before scanning, and any construct the stripper does not understand
(for example dollar-quoted strings) leaves its keywords visible to the scan,
which can only cause a false rejection, never a false acceptance.
"""

import re

FORBIDDEN_KEYWORDS: frozenset[str] = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "upsert",
        "create",
        "drop",
        "alter",
        "truncate",
        "attach",
        "detach",
        "copy",
        "export",
        "import",
        "install",
        "load",
        "force",
        "call",
        "pragma",
        "set",
        "reset",
        "begin",
        "commit",
        "rollback",
        "transaction",
        "vacuum",
        "checkpoint",
        "analyze",
        "use",
        "grant",
        "revoke",
        "execute",
        "prepare",
        "deallocate",
    }
)

_WORD_RE = re.compile(r"[a-z_][a-z0-9_]*")
_FIRST_KEYWORD_RE = re.compile(r"^\s*([a-z_]+)")


class SQLValidationError(ValueError):
    """Raised when a SQL string fails the read-only check."""


def _strip_comments_and_strings(sql: str) -> str:
    """Replace string literals, quoted identifiers, and comments with spaces.

    Keeps character positions roughly stable so that the remaining text can be
    scanned for keywords without matching words that only appear inside
    literals or comments.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'" and sql[i : i + 2] != "''":
                    break
                if sql[i : i + 2] == "''":
                    i += 1
                i += 1
            i += 1
            out.append(" ")
        elif ch == '"':
            i += 1
            while i < n and sql[i] != '"':
                i += 1
            i += 1
            out.append(" ")
        elif two == "--":
            while i < n and sql[i] != "\n":
                i += 1
        elif two == "/*":
            i += 2
            while i < n and sql[i : i + 2] != "*/":
                i += 1
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_read_only(sql: str) -> str:
    """Validate that a SQL string is a single read-only statement.

    Returns the original SQL unchanged when it passes. Raises
    SQLValidationError with a human-readable reason when it does not.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SQLValidationError("SQL must be a non-empty string")

    stripped = _strip_comments_and_strings(sql).lower()
    body = stripped.strip()
    if not body:
        raise SQLValidationError("SQL contains no statement")

    body = body.rstrip(";").rstrip()
    if ";" in body:
        raise SQLValidationError("only a single SQL statement is allowed")

    first = _FIRST_KEYWORD_RE.match(body)
    if first is None or first.group(1) not in ("select", "with"):
        raise SQLValidationError("only SELECT or WITH ... SELECT statements are allowed")

    for word in _WORD_RE.findall(body):
        if word in FORBIDDEN_KEYWORDS:
            raise SQLValidationError(f"forbidden keyword in SQL: {word.upper()}")

    return sql
