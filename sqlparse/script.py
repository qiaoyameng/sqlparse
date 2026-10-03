#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Splitting of SQL scripts that use client-side ``DELIMITER`` directives.

The :func:`split` function and the :class:`ScriptSplitter` class provide an
explicit script-splitting mode in addition to the regular
:func:`sqlparse.split` behavior.  They understand the MySQL client command
``DELIMITER``, which is not SQL itself but a client instruction that changes
the sequence of characters terminating SQL statements (for example
``DELIMITER $$`` is needed so that the semicolons inside a stored procedure
body stay part of the ``CREATE PROCEDURE`` statement).

Delimiter directives are only recognized between statements.  Occurrences of
the directive word inside string literals, quoted identifiers (backticks or
double quotes) or comments are treated as ordinary content.  The active
delimiter may be switched any number of times, including to a multi-character
sequence.  Feeding the same script character by character (or in arbitrary
chunks) yields exactly the same item boundaries as feeding it at once.

Both :func:`split` and :class:`ScriptSplitter` preserve the original text:
concatenating ``item.value`` of all returned items reproduces the input when
``split`` is called with ``strip=False``.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import TextIOBase

from sqlparse.exceptions import SQLParseError

__all__ = [
    'DelimiterDirective',
    'ScriptItem',
    'ScriptSplitter',
    'SqlStatement',
    'split',
    'split_iter',
]


class ScriptItem:
    """Base class for elements returned by :class:`ScriptSplitter`.

    Attributes:
        value: Original text spanned by the item.
        offset: Zero-based character offset of the item start.
        line: One-based line number of the item start.
        column: One-based column number of the item start.
    """

    is_directive = False

    def __init__(self, value: str, offset: int, line: int, column: int):
        self.value = value
        self.offset = offset
        self.line = line
        self.column = column

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        kind = ' directive' if self.is_directive else ''
        return (f'{type(self).__name__}{kind}({self.value!r}, '
                f'line={self.line}, column={self.column})')

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ScriptItem):
            return NotImplemented
        return (type(self) is type(other)
                and self.value == other.value
                and self.offset == other.offset)

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.value, self.offset))


class SqlStatement(ScriptItem):
    """An SQL statement terminated by the active delimiter.

    ``delimiter`` holds the terminating delimiter sequence or ``None`` for a
    trailing statement without a terminator.  ``value`` includes the
    terminator and the whitespace/comments attached to the item.
    """

    def __init__(self, value: str, offset: int, line: int, column: int,
                 delimiter: str | None):
        super().__init__(value, offset, line, column)
        self.delimiter = delimiter


class DelimiterDirective(ScriptItem):
    """A ``DELIMITER`` directive found between statements.

    ``new_delimiter`` is the delimiter sequence activated by the directive
    (for example ``$$`` after ``DELIMITER $$``).
    """

    is_directive = True

    def __init__(self, value: str, offset: int, line: int, column: int,
                 new_delimiter: str):
        super().__init__(value, offset, line, column)
        self.new_delimiter = new_delimiter


# Scanner states.
_GAP = 'gap'        # whitespace/comments between statements
_TEXT = 'text'      # statement body
_SQUOTE = 'squote'  # inside '...'
_DQUOTE = 'dquote'  # inside "..."
_BTICK = 'btick'    # inside `...`
_LINE = 'line'      # inside a -- or # line comment
_BLOCK = 'block'    # inside a /* ... */ block comment
_WORD = 'word'      # probing a candidate DELIMITER word
_DIR = 'dir'        # collecting a DELIMITER argument

_QUOTE_STATES = {
    "'": _SQUOTE,
    '"': _DQUOTE,
    '`': _BTICK,
}
_QUOTE_CHARS = {
    _SQUOTE: "'",
    _DQUOTE: '"',
    _BTICK: '`',
}
_DIRECTIVE_WORD = 'DELIMITER'
_DEFAULT_DELIMITER = ';'


def _kmp_table(pattern: str) -> list[int]:
    """KMP failure function for *pattern*."""
    table = [0] * len(pattern)
    k = 0
    for i in range(1, len(pattern)):
        while k and pattern[i] != pattern[k]:
            k = table[k - 1]
        if pattern[i] == pattern[k]:
            k += 1
        table[i] = k
    return table




class ScriptSplitter:
    """Split an SQL script into statements and ``DELIMITER`` directives.

    The splitter is stateful: :meth:`feed` accepts incremental input and
    returns the items that became complete while processing the chunk.
    Feeding a script one character at a time yields exactly the same item
    boundaries as feeding it at once.  After all input has been fed, call
    :meth:`close` to flush a trailing statement or to detect an
    unterminated/invalid directive.

    Occurrences of ``DELIMITER`` inside strings, backtick/double-quoted
    identifiers and comments are ordinary content.  The delimiter can be
    switched any number of times and may be a multi-character sequence.

    :param delimiter: Initial statement terminator (default ``";"``).
    :raises SQLParseError: if a ``DELIMITER`` directive lacks an argument.
        The error message carries line, column and offset information.
    """

    def __init__(self, delimiter: str = _DEFAULT_DELIMITER):
        if not isinstance(delimiter, str) or not delimiter:
            raise ValueError('delimiter must be a non-empty string')
        self._initial_delimiter = delimiter
        self._reset()

    def _reset(self) -> None:
        self._delimiter = self._initial_delimiter
        self._kmp = _kmp_table(self._initial_delimiter)
        self._state = _GAP
        # GAP when the current construct sits between statements, TEXT when
        # it belongs to the statement body.
        self._origin = _GAP
        # One lookahead char awaiting the following input char: tuple
        # (char, state, origin, match_len, kind).
        self._pend = None
        self._match = 0        # KMP matched prefix length
        self._probe = 0        # DELIMITER probe matched length
        # _head: whitespace/comments/directive text before the current not
        # yet emitted item; _body: the current statement text.
        self._head: list[str] = []
        self._body: list[str] = []
        self._arg_chars: list[str] = []
        self._word_idx = 0
        self._dir_end_idx = 0
        self._head_start = (0, 1, 1)
        self._stmt_loc = None
        self._word_loc = None
        self._held: ScriptItem | None = None
        self._out: list[ScriptItem] = []
        self._pos = 0
        self._line = 1
        self._col = 1

    @property
    def delimiter(self) -> str:
        """The currently active statement delimiter."""
        return self._delimiter

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------
    def _snapshot(self) -> tuple[int, int, int]:
        return self._pos, self._line, self._col

    def _commit(self, ch: str, newline: bool) -> None:
        self._pos += 1
        if newline:
            self._line += 1
            self._col = 1
        else:
            self._col += 1

    def _error(self, loc: tuple[int, int, int], message: str) -> None:
        pos, line, col = loc
        raise SQLParseError(
            f'{message} at line {line}, column {col} (offset {pos})')

    # ------------------------------------------------------------------
    # Buffers / output
    # ------------------------------------------------------------------
    def _put_head(self, ch: str) -> None:
        if not self._head:
            self._head_start = self._snapshot()
        self._head.append(ch)

    def _buffer(self, ch: str) -> None:
        """Buffer a char according to where its construct originated."""
        if self._origin == _GAP:
            self._put_head(ch)
        else:
            self._body.append(ch)

    def _set_pending(self, ch: str, kind: str) -> None:
        self._pend = (ch, self._state, self._origin, self._match, kind)

    def _start_statement(self, ch: str) -> None:
        self._origin = _TEXT
        self._stmt_loc = self._snapshot()
        self._body = [ch]
        self._state = _TEXT
        self._match = 0

    def _finish_statement(self, terminator: str | None) -> None:
        value = ''.join(self._head) + ''.join(self._body)
        loc = self._stmt_loc or self._head_start
        item = SqlStatement(value, loc[0], loc[1], loc[2], terminator)
        if self._held is not None:
            self._out.append(self._held)
        self._held = item
        self._head = []
        self._body = []
        self._stmt_loc = None
        self._origin = _GAP
        self._match = 0
        self._state = _GAP

    def _finish_directive(self) -> None:
        arg = ''.join(self._arg_chars).strip()
        if not arg:
            self._error(self._word_loc,
                        'Invalid DELIMITER directive: '
                        'missing delimiter sequence')
        # The directive ends with its argument; any following newline is
        # inter-item whitespace and must not be part of its value.
        end_idx = self._dir_end_idx or len(self._head)
        loc = self._word_loc
        value = ''.join(self._head[self._word_idx:end_idx])
        if self._held is None:
            loc = self._head_start
            value = ''.join(self._head[:end_idx])
        item = DelimiterDirective(value, loc[0], loc[1], loc[2], arg)
        if self._held is not None:
            self._held.value += ''.join(self._head[:self._word_idx])
            self._out.append(self._held)
        self._held = item
        self._head = self._head[end_idx:]
        self._body = []
        self._arg_chars = []
        self._stmt_loc = None
        self._word_idx = 0
        self._word_loc = None
        self._probe = 0
        self._match = 0
        self._origin = _GAP
        self._delimiter = arg
        self._kmp = _kmp_table(arg)
        self._state = _GAP

    def _abort_probe(self, ch: str | None) -> None:
        """A probed word is ordinary text: promote it to a statement body."""
        word = self._head[self._word_idx:]
        self._head = self._head[:self._word_idx]
        self._origin = _TEXT
        self._stmt_loc = self._word_loc
        self._body = list(word)
        self._word_idx = 0
        self._word_loc = None
        self._probe = 0
        self._state = _TEXT
        self._match = 0
        completed = False
        for c in self._body:
            if self._kmp_step(c):
                completed = True
        if completed:
            self._finish_statement(self._delimiter)
        elif ch is not None:
            self._text_char(ch)

    # ------------------------------------------------------------------
    # KMP
    # ------------------------------------------------------------------
    def _kmp_step(self, ch: str) -> bool:
        pattern = self._delimiter
        k = self._match
        while k and ch != pattern[k]:
            k = self._kmp[k - 1]
        if ch == pattern[k]:
            k += 1
        self._match = k
        if k == len(pattern):
            self._match = 0
            return True
        return False

    def _text_body(self, ch: str) -> None:
        self._body.append(ch)
        if self._kmp_step(ch):
            self._finish_statement(self._delimiter)

    # ------------------------------------------------------------------
    # Per-state handlers (ch is never CR here)
    # ------------------------------------------------------------------
    def _gap_char(self, ch: str) -> None:
        if ch.isspace():
            self._put_head(ch)
        elif ch == '#':
            self._origin = _GAP
            self._put_head(ch)
            self._state = _LINE
        elif ch in ('/', '-'):
            self._put_head(ch)
            self._set_pending(ch, 'gap-comment')
        elif ch in ("'", '"', '`'):
            self._origin = _TEXT
            self._stmt_loc = self._snapshot()
            self._body = [ch]
            self._state = _QUOTE_STATES[ch]
            self._match = 0
        elif ch in 'dD':
            self._word_idx = len(self._head)
            self._word_loc = self._snapshot()
            self._put_head(ch)
            self._probe = 1
            self._state = _WORD
        else:
            self._start_statement(ch)

    def _text_char(self, ch: str) -> None:
        if ch in ("'", '"', '`'):
            self._body.append(ch)
            self._origin = _TEXT
            self._state = _QUOTE_STATES[ch]
            self._match = 0
        elif ch == '#':
            self._body.append(ch)
            self._origin = _TEXT
            self._state = _LINE
        elif ch in ('/', '-'):
            # Feed to KMP first: if it completes the delimiter the
            # statement ends here and no comment lookahead is needed.
            self._body.append(ch)
            if self._kmp_step(ch):
                self._finish_statement(self._delimiter)
            else:
                # Hold pending in case it opens a comment instead.
                self._set_pending(ch, 'text-comment')
        else:
            self._text_body(ch)

    def _quote_char(self, ch: str) -> None:
        quote = _QUOTE_CHARS[self._state]
        if ch == quote:
            self._body.append(ch)
            self._set_pending(ch, 'quote')
        elif ch == '\\':
            self._body.append(ch)
            self._set_pending(ch, 'backslash')
        else:
            self._body.append(ch)

    def _line_char(self, ch: str) -> None:
        self._buffer(ch)
        if ch == '\n':
            self._state = self._origin

    def _block_char(self, ch: str) -> None:
        if ch == '*':
            self._buffer(ch)
            self._set_pending(ch, 'star')
        else:
            self._buffer(ch)

    def _word_char(self, ch: str) -> None:
        if self._probe < len(_DIRECTIVE_WORD):
            if ch.upper() == _DIRECTIVE_WORD[self._probe]:
                self._probe += 1
                self._put_head(ch)
            else:
                self._abort_probe(ch)
            return
        if ch.isspace():
            self._put_head(ch)
            self._arg_chars = []
            self._dir_end_idx = 0
            self._state = _DIR
        else:
            self._abort_probe(ch)

    def _dir_char(self, ch: str) -> None:
        if ch == '\n':
            self._finish_directive()
            self._put_head(ch)
        else:
            self._put_head(ch)
            self._arg_chars.append(ch)
            if not ch.isspace():
                self._dir_end_idx = len(self._head)

    # ------------------------------------------------------------------
    # Pending resolution
    # ------------------------------------------------------------------
    def _resolve(self, nxt: str) -> None:
        ch, state, origin, match, kind = self._pend
        self._pend = None
        if kind in ('gap-comment', 'text-comment'):
            opening = (ch == '/' and nxt == '*') or \
                      (ch == '-' and nxt == '-')
            if opening:
                self._state = _LINE if ch == '-' else _BLOCK
                self._origin = origin
                self._match = 0
                # Both chars belong to the comment token.
                self._buffer(nxt)
            else:
                # Not a comment opener: current state (GAP or TEXT) already
                # reflects the pending char and its KMP progress.  Dispatching
                # may complete a statement, so do not restore stale state.
                if self._origin == _TEXT:
                    self._text_char(nxt)
                else:
                    self._gap_char(nxt)
        elif kind == 'star':
            if nxt == '/':
                self._buffer(nxt)
                self._state = origin
                self._match = 0
            else:
                # Stay inside the block comment.
                self._state = _BLOCK
                self._dispatch(nxt)
        elif kind == 'quote':
            if nxt == ch:
                # Doubled quote: literal quote, remain inside.
                self._state = state
                self._body.append(nxt)
            else:
                # Pending quote closes the literal; following char is body.
                self._state = _TEXT
                self._origin = origin
                self._match = 0
                self._text_char(nxt)
        elif kind == 'backslash':
            self._state = state
            self._body.append(nxt)

    def _resolve_pending_eof(self) -> None:
        if self._pend is None:
            return
        ch, state, origin, match, kind = self._pend
        self._pend = None
        self._state = state
        self._origin = origin
        self._match = match
        if kind == 'quote':
            self._state = _TEXT

    # ------------------------------------------------------------------
    # Input loop
    # ------------------------------------------------------------------
    def _dispatch(self, ch: str) -> None:
        if self._state == _GAP:
            self._gap_char(ch)
        elif self._state == _TEXT:
            self._text_char(ch)
        elif self._state in (_SQUOTE, _DQUOTE, _BTICK):
            self._quote_char(ch)
        elif self._state == _LINE:
            self._line_char(ch)
        elif self._state == _BLOCK:
            self._block_char(ch)
        elif self._state == _WORD:
            self._word_char(ch)
        elif self._state == _DIR:
            self._dir_char(ch)

    def _newline(self, ch: str) -> None:
        if self._state == _DIR:
            if self._dir_end_idx == 0:
                self._dir_end_idx = len(self._head)
            self._finish_directive()
            self._put_head(ch)
        elif self._state == _LINE:
            self._buffer(ch)
            self._state = self._origin
        else:
            self._buffer(ch)

    def _read(self, ch: str) -> None:
        # Resolve a pending lookahead first.
        if self._pend is not None:
            if self._pend[4] == 'cr':
                # CR is a newline. A following LF completes a CRLF pair:
                # both characters are kept, but the pair counts once.
                self._pend = None
                self._newline('\r')
                self._commit('\r', True)
                if ch == '\n':
                    if self._state == _GAP:
                        self._put_head('\n')
                    else:
                        self._buffer('\n')
                    # LF is consumed as part of the CRLF newline.
                    self._pos += 1
                else:
                    self._dispatch(ch)
                    self._commit(ch, ch == '\n')
                return
            self._resolve(ch)
            self._commit(ch, ch == '\n')
            return
        if ch == '\r':
            self._pend = (ch, self._state, self._origin, self._match, 'cr')
            return
        if ch == '\n':
            self._newline(ch)
            self._commit(ch, True)
            return
        self._dispatch(ch)
        self._commit(ch, False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def feed(self, chunk: str) -> list[ScriptItem]:
        """Process *chunk* and return the items completed in this call.

        *chunk* may be any string, including a single character.  Splitting a
        script into chunks at arbitrary positions does not change the
        resulting item boundaries.
        """
        if not isinstance(chunk, str):
            raise TypeError(f'Expected str, got {type(chunk)!r}')
        for ch in chunk:
            self._read(ch)
        items = self._out
        self._out = []
        return items

    def close(self) -> list[ScriptItem]:
        """Finish processing and return the remaining items.

        :raises SQLParseError: on a bare/unterminated ``DELIMITER``.
        """
        if self._pend is not None and self._pend[4] == 'cr':
            ch, state, origin, match, kind = self._pend
            self._pend = None
            self._newline('\r')
            self._commit('\r', True)
        self._resolve_pending_eof()
        if self._state == _WORD:
            if self._probe == len(_DIRECTIVE_WORD):
                self._error(self._word_loc,
                            'Invalid DELIMITER directive: '
                            'missing delimiter sequence')
            self._abort_probe(None)
        if self._state == _DIR:
            if self._dir_end_idx == 0:
                self._dir_end_idx = len(self._head)
            self._finish_directive()
        if self._body:
            self._finish_statement(None)
        if self._held is not None:
            if not self._held.is_directive:
                # Trailing whitespace/comments belong to the last SQL
                # statement; a directive must end with its argument.
                self._held.value += ''.join(self._head)
            self._out.append(self._held)
            self._held = None
            self._head = []
        elif self._head and ''.join(self._head).strip():
            pos, line, col = self._head_start
            self._out.append(SqlStatement(''.join(self._head), pos, line,
                                          col, None))
        items = self._out
        self._out = []
        return items


def split_iter(
    script: str,
    encoding: str | None = None,
    delimiter: str = _DEFAULT_DELIMITER,
) -> Iterator[ScriptItem]:
    """Split *script* lazily into :class:`ScriptItem` objects.

    :param script: A string or file-like object containing SQL statements
        and ``DELIMITER`` directives; bytes are decoded with *encoding*.
    :param encoding: Encoding used when *script* is bytes (default utf-8).
    :param delimiter: Initial statement terminator.
    """
    if isinstance(script, TextIOBase):
        script = script.read()
    if isinstance(script, bytes):
        script = script.decode(encoding or 'utf-8')
    if not isinstance(script, str):
        raise TypeError(f'Expected text or file-like object, '
                        f'got {type(script)!r}')
    splitter = ScriptSplitter(delimiter=delimiter)
    yield from splitter.feed(script)
    yield from splitter.close()


def split(
    script: str,
    encoding: str | None = None,
    delimiter: str = _DEFAULT_DELIMITER,
    strip: bool = True,
) -> list[ScriptItem]:
    """Split *script* into SQL statements and ``DELIMITER`` directives.

    In contrast to :func:`sqlparse.split` (whose behavior is unchanged),
    this explicit script mode recognizes the MySQL client ``DELIMITER``
    directive: its argument becomes the statement terminator until the next
    directive.  Directives are only recognized between statements; the same
    word inside strings, quoted identifiers or comments is ordinary
    content.  Multi-character delimiters and repeated switches are
    supported.

    :param script: A string containing SQL statements and directives.
    :param encoding: Encoding used when *script* is bytes (optional).
    :param delimiter: Initial statement terminator (default ``";"``).
    :param strip: If True (default), leading/trailing whitespace is removed
        from returned SQL statement values; directive values are never
        stripped.  Use False to get exact original text for every item.
    :returns: A list of :class:`SqlStatement` and
        :class:`DelimiterDirective` objects; check ``item.is_directive`` to
        distinguish directives from SQL.
    :raises SQLParseError: for invalid or unterminated directives, with a
        locatable line, column and offset.
    """
    items = list(split_iter(script, encoding, delimiter))
    if strip:
        for item in items:
            if not item.is_directive:
                item.value = item.value.strip()
    return items
