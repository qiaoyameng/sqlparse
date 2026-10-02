#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Parse SQL statements."""

# Setup namespace
from collections.abc import Generator
from typing import IO, Any

from sqlparse import cli, engine, filters, formatter, sql, tokens
from sqlparse.lexer import (
    Lexer,
    LexerBuilder,
    LexerConfig,
    LexerConfigBuilder,
    resolve_lexer,
)

__version__ = "0.6.1.dev0"
__all__ = [
    "Lexer",
    "LexerBuilder",
    "LexerConfig",
    "LexerConfigBuilder",
    "cli",
    "engine",
    "filters",
    "formatter",
    "sql",
    "tokens",
]


def parse(
    sql: str, encoding: str | None = None, *, lexer: Lexer | None = None
) -> tuple[sql.Statement, ...]:
    """Parse sql and return a list of statements.

    :param sql: A string containing one or more SQL statements.
    :param encoding: The encoding of the statement (optional).
    :param lexer: A frozen lexical configuration (optional).
    :returns: A tuple of :class:`~sqlparse.sql.Statement` instances.
    """
    return tuple(parsestream(sql, encoding, lexer=lexer))


def parsestream(
    stream: str | IO[str],
    encoding: str | None = None,
    *,
    lexer: Lexer | None = None,
) -> Generator[sql.Statement, None, None]:
    """Parses sql statements from file-like object.

    :param stream: A file-like object.
    :param encoding: The encoding of the stream contents (optional).
    :param lexer: A frozen lexical configuration (optional).
    :returns: A generator of :class:`~sqlparse.sql.Statement` instances.
    """
    active_lexer = resolve_lexer(lexer)
    stack = engine.FilterStack(lexer=active_lexer)
    stack.enable_grouping()
    return stack.run(stream, encoding)


def format(
    sql: str,
    encoding: str | None = None,
    *,
    lexer: Lexer | None = None,
    **options: Any,
) -> str:
    """Format *sql* according to *options*.

    Available options are documented in :ref:`formatting`.

    In addition to the formatting options this function accepts the
    keyword "encoding" which determines the encoding of the statement.

    :returns: The formatted SQL statement as string.
    """
    stack = engine.FilterStack(lexer=resolve_lexer(lexer))
    options = formatter.validate_options(options)
    stack = formatter.build_filter_stack(stack, options)
    stack.postprocess.append(filters.SerializerUnicode())
    return "".join(stack.run(sql, encoding))


def split(
    sql: str,
    encoding: str | None = None,
    strip_semicolon: bool = False,
    *,
    lexer: Lexer | None = None,
) -> list[str]:
    """Split *sql* into single statements.

    :param sql: A string containing one or more SQL statements.
    :param encoding: The encoding of the statement (optional).
    :param strip_semicolon: If True, remove trailing semicolons
        (default: False).
    :returns: A list of strings.
    """
    stack = engine.FilterStack(
        strip_semicolon=strip_semicolon,
        lexer=resolve_lexer(lexer),
    )
    return [str(stmt).strip() for stmt in stack.run(sql, encoding)]
