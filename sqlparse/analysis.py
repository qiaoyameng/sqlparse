#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Fault-tolerant lexical analysis for incomplete SQL input.

This module targets clients that see SQL while it is still being typed,
e.g. online editors.  Unlike :func:`sqlparse.parse` it does not assume
the input is finished: unterminated string literals, quoted names,
block comments and dollar-quoted literals are reported as lexical
diagnostics together with the tokens recognized so far.

This is a *lexical* analysis only.  It deliberately does not try to
validate the syntax of the statement, and it never invents recovery
points: an unterminated construct extends to the end of the input, so
e.g. a semicolon inside an unclosed string is not mistaken for a
statement boundary.
"""

from dataclasses import dataclass, field

from sqlparse import keywords, tokens as T
from sqlparse.lexer import Lexer, _coerce_text
from sqlparse.utils import consume

#: Token types produced for unterminated quoted-name openers.
_QUOTED_NAME_OPENERS = {
    '"': T.String.Symbol,
    '`': T.Name,
    '´': T.Name,
}

#: Diagnostic kinds.
UNCLOSED_STRING = 'unclosed_string'
UNCLOSED_QUOTED_NAME = 'unclosed_quoted_name'
UNCLOSED_BLOCK_COMMENT = 'unclosed_block_comment'
UNCLOSED_DOLLAR_QUOTE = 'unclosed_dollar_quote'


@dataclass(frozen=True)
class Diagnostic:
    """A single lexical problem found in the input.

    ``start`` is the offset of the opening delimiter in the original
    text, ``end`` the offset the unterminated construct runs to (the
    end of the input).  ``opener`` is the exact opening delimiter,
    e.g. ``"'"``, ``"/*"`` or ``"$tag$"``.
    """

    kind: str
    start: int
    end: int
    opener: str
    message: str


@dataclass
class AnalysisResult:
    """Result of :func:`analyze`.

    ``text`` is the original input, ``tokens`` the flat list of
    ``(token type, value)`` pairs recognized so far and ``diagnostics``
    the lexical problems found.  ``final`` records whether the input
    was analyzed as finished.
    """

    text: str
    tokens: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    final: bool = False

    @property
    def status(self):
        """``'ok'``, ``'pending'`` or ``'error'``.

        Unterminated constructs are reported as ``'pending'`` while
        more input may still complete them (``final=False``) and as
        ``'error'`` once the input is known to be finished
        (``final=True``).
        """
        if not self.diagnostics:
            return 'ok'
        return 'error' if self.final else 'pending'

    @property
    def ok(self):
        """True if no lexical problems were found."""
        return not self.diagnostics


def _scan(text, lexer):
    """Tokenize `text` like the lexer does, collecting diagnostics.

    Mirrors ``Lexer.get_tokens``, but an opener whose closing delimiter
    is missing produces a :class:`Diagnostic` and swallows the rest of
    the input as a single token instead of degrading into ``Error``
    characters.  Recovery stops there: no attempt is made to re-sync on
    semicolons or newlines inside the unterminated construct.
    """
    delimited_spans = keywords.find_delimited_spans(text)
    span_openers = delimited_spans.openers

    out = []
    diagnostics = []
    iterable = enumerate(text)
    for pos, char in iterable:
        if pos in span_openers:
            resolved = delimited_spans.resolve(pos)
            if resolved is not None:
                end, ttype = resolved
                out.append((ttype, text[pos:end]))
                consume(iterable, end - pos - 1)
                continue
            # Unterminated block comment or dollar-quoted literal.
            opener_end, _tag, ttype = delimited_spans.openers[pos]
            opener = text[pos:opener_end]
            if ttype in (T.Comment.Multiline, T.Comment.Multiline.Hint):
                kind = UNCLOSED_BLOCK_COMMENT
                what = 'block comment'
            else:
                kind = UNCLOSED_DOLLAR_QUOTE
                what = 'dollar-quoted literal'
            diagnostics.append(Diagnostic(
                kind, pos, len(text), opener,
                f'unterminated {what} opened by {opener!r} '
                f'at offset {pos}'))
            out.append((ttype, text[pos:]))
            break

        for rexmatch, action in lexer._SQL_REGEX:
            m = rexmatch(text, pos)

            if not m:
                continue
            elif isinstance(action, T._TokenType):
                out.append((action, m.group()))
            elif action is keywords.PROCESS_AS_KEYWORD:
                out.append(lexer.is_keyword(m.group()))

            consume(iterable, m.end() - pos - 1)
            break
        else:
            if char == "'":
                kind, ttype = UNCLOSED_STRING, T.String.Single
            elif char in _QUOTED_NAME_OPENERS:
                kind = UNCLOSED_QUOTED_NAME
                ttype = _QUOTED_NAME_OPENERS[char]
            else:
                out.append((T.Error, char))
                continue
            diagnostics.append(Diagnostic(
                kind, pos, len(text), char,
                f'unterminated literal opened by {char!r} '
                f'at offset {pos}'))
            out.append((ttype, text[pos:]))
            break
    return out, diagnostics


def analyze(sql, encoding=None, final=False):
    """Lexically analyze possibly incomplete SQL.

    :param sql: A string (or bytes, or file-like object) containing SQL
        that may still be incomplete.
    :param encoding: The encoding of ``sql`` if it is given as bytes
        (optional).
    :param final: If ``True`` the input is treated as finished and
        unterminated constructs are reported as errors.  If ``False``
        (the default) they are reported as *pending*, i.e. more input
        may still complete them.
    :returns: An :class:`AnalysisResult` with the original text, the
        recognized ``(token type, value)`` pairs and a list of
        :class:`Diagnostic` objects.

    Note that this is a lexical analysis only; it does not check
    whether the tokens form a valid SQL statement.
    """
    text = _coerce_text(sql, encoding)
    lexer = Lexer.get_default_instance()
    token_list, diagnostics = _scan(text, lexer)
    return AnalysisResult(text, token_list, diagnostics, final)
