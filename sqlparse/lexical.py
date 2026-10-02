#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Fault-tolerant lexical analysis for possibly incomplete SQL input.

Online editors and similar tools often need to inspect SQL that is still
being typed. :func:`analyze` tokenizes such input like the regular lexer,
but instead of degrading unterminated constructs into a stream of
meaningless tokens it reports them as diagnostics:

* unclosed string literals or quoted names (``'...``, ``"...``, `` `...``),
* unclosed block comments (``/*...``, ``/*+...``),
* unclosed dollar-quoted literals (``$$...``, ``$tag$...``).

Recovery has a fixed boundary: an unterminated construct consumes the
rest of the input. Nothing after its opener is reinterpreted as SQL, so
e.g. a semicolon inside an unterminated string is never treated as a
statement boundary to resynchronize on.

Block comments do not nest: the first ``*/`` closes the comment, no
matter how many ``/*`` openers were seen before it.

Set ``final=False`` when more input may still be appended. Diagnostics
are then marked as *pending* (the user may simply not have typed the
closing delimiter yet) instead of errors.
"""

from dataclasses import dataclass, field

from sqlparse import keywords
from sqlparse import tokens as T
from sqlparse.lexer import Lexer, coerce_to_text
from sqlparse.utils import consume

#: Kind of :class:`Diagnostic` for unterminated strings and quoted names.
UNCLOSED_STRING = 'unclosed_string'
#: Kind of :class:`Diagnostic` for unterminated block comments.
UNCLOSED_BLOCK_COMMENT = 'unclosed_block_comment'
#: Kind of :class:`Diagnostic` for unterminated dollar-quoted literals.
UNCLOSED_DOLLAR_QUOTE = 'unclosed_dollar_quote'

# Quote characters that may open a string literal or quoted name, mapped
# to the token type emitted for the unterminated rest of the input.
_QUOTE_OPENERS = {
    "'": T.String.Single,
    '"': T.String.Symbol,
    '`': T.Name,
    '´': T.Name,
}


@dataclass(frozen=True)
class Diagnostic:
    """A lexical problem found by :func:`analyze`.

    ``start`` is the offset of the opening delimiter in the analyzed
    text; the original text itself is available as
    :attr:`LexicalAnalysis.text`. ``opener`` is the delimiter that was
    never closed, e.g. ``"'"``, ``"/*"`` or ``"$tag$"``.
    """

    kind: str
    start: int
    opener: str
    #: True if the input may still be appended to (``final=False``), so
    #: the missing delimiter may simply not have been typed yet.
    pending: bool = False

    @property
    def message(self) -> str:
        """Human-readable description of the problem."""
        if self.kind == UNCLOSED_STRING:
            what = 'string literal' if self.opener == "'" else 'quoted name'
        elif self.kind == UNCLOSED_BLOCK_COMMENT:
            what = 'block comment'
        else:
            what = 'dollar-quoted literal'
        msg = (f"unterminated {what} opened by {self.opener!r} "
               f"at offset {self.start}")
        if self.pending:
            msg += '; input may still be continued'
        return msg


@dataclass
class LexicalAnalysis:
    """Result of :func:`analyze`.

    ``tokens`` is a list of ``(token type, value)`` pairs, like the
    stream returned by :func:`sqlparse.lexer.tokenize`. Concatenating
    the values always yields :attr:`text` again.
    """

    #: The analyzed text, exactly as given (after decoding).
    text: str
    #: True if the input is final and no more text will be appended.
    final: bool
    #: The ``(token type, value)`` pairs the input was split into.
    tokens: list = field(default_factory=list)
    #: Lexical problems found in the input, in order of appearance.
    diagnostics: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if no lexical problems were found."""
        return not self.diagnostics

    @property
    def pending(self) -> bool:
        """True if the input ends in an unterminated construct but may
        still be continued (only possible when ``final=False``)."""
        return bool(self.diagnostics) and not self.final


def _scan(text, final):
    """Tokenize `text`, recovering from unterminated constructs.

    Mirrors ``Lexer.get_tokens``, but an opener whose closing delimiter
    is missing swallows the rest of the input as a single token and is
    reported as a diagnostic instead of falling back to ``Error`` tokens.
    """
    lexer = Lexer.get_default_instance()
    scanned = []
    diagnostics = []
    delimited_spans = keywords.find_delimited_spans(text)
    span_openers = delimited_spans.openers

    iterable = enumerate(text)
    for pos, char in iterable:
        if pos in span_openers:
            resolved = delimited_spans.resolve(pos)
            if resolved is not None:
                end, ttype = resolved
                scanned.append((ttype, text[pos:end]))
                consume(iterable, end - pos - 1)
                continue
            # Unterminated block comment or dollar-quoted literal: the
            # rest of the input belongs to the construct, nothing after
            # the opener (including semicolons) is reinterpreted as SQL.
            opener_end, _tag, ttype = span_openers[pos]
            kind = (UNCLOSED_BLOCK_COMMENT if ttype in T.Comment.Multiline
                    else UNCLOSED_DOLLAR_QUOTE)
            scanned.append((ttype, text[pos:]))
            diagnostics.append(Diagnostic(
                kind=kind, start=pos, opener=text[pos:opener_end],
                pending=not final))
            break

        for rexmatch, action in lexer._SQL_REGEX:
            m = rexmatch(text, pos)

            if not m:
                continue
            elif isinstance(action, T._TokenType):
                scanned.append((action, m.group()))
            elif action is keywords.PROCESS_AS_KEYWORD:
                scanned.append(lexer.is_keyword(m.group()))

            consume(iterable, m.end() - pos - 1)
            break
        else:
            ttype = _QUOTE_OPENERS.get(char)
            if ttype is None:
                scanned.append((T.Error, char))
                continue
            # Unterminated string literal or quoted name: swallows the
            # rest of the input, see the comment above.
            scanned.append((ttype, text[pos:]))
            diagnostics.append(Diagnostic(
                kind=UNCLOSED_STRING, start=pos, opener=char,
                pending=not final))
            break
    return scanned, diagnostics


def analyze(sql, encoding=None, final=True):
    """Lexically analyze `sql`, tolerating incomplete input.

    Returns a :class:`LexicalAnalysis` with the tokens and a list of
    :class:`Diagnostic` items explaining unterminated string literals,
    block comments or dollar-quoted literals.

    :param sql: A string, bytes or a file-like object with SQL text.
    :param encoding: The encoding of ``sql`` if given as bytes (optional).
    :param final: If True (default), the input is considered complete and
        unterminated constructs are reported as errors. If False, more
        input may still be appended and diagnostics are marked pending.
    """
    text = coerce_to_text(sql, encoding)
    scanned, diagnostics = _scan(text, final)
    return LexicalAnalysis(text=text, final=final,
                           tokens=scanned, diagnostics=diagnostics)
