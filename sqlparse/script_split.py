#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Explicit script splitting for client-side ``DELIMITER`` directives.

MySQL compatible clients (the ``mysql`` CLI, mysqldump exports, ...) can
switch the statement terminator from within a script::

    DELIMITER $$
    CREATE PROCEDURE p()
    BEGIN
      SELECT 1;
    END$$
    DELIMITER ;

The directive is not part of SQL.  It is processed by the client and is
only recognised when it stands alone at a statement boundary; the same
word inside a string literal, a quoted identifier or a comment is
ordinary content.

:class:`ScriptSplitter` is a small incremental state machine.  It can be
fed a whole script at once or one character at a time, and both produce
identical segment boundaries.
"""

import re
from collections import namedtuple
from io import TextIOBase

__all__ = [
    "ScriptSegment",
    "ScriptSplitter",
    "split_script",
    "split_script_statements",
]

DEFAULT_DELIMITER = ";"

_NORMAL = "normal"
_STRING = "string"
_LINE_COMMENT = "line-comment"
_BLOCK_COMMENT = "block-comment"
_DASH = "dash"
_SLASH = "slash"
_STAR = "star"

_WS = frozenset(" \t\r\n")
_HORIZONTAL_WS = frozenset(" \t")
_NEWLINES = frozenset("\r\n")
_WORD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_"
)
_DIRECTIVE_RE = re.compile(r"delimiter(?=\W)", re.IGNORECASE)
_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_WORD_RE = re.compile(r"[A-Za-z0-9_]*")


class ScriptSegment(namedtuple("ScriptSegment",
                              "kind value start end delimiter")):
    """A piece of a split script.

    :param kind: ``"sql"`` for a SQL statement (terminated by the active
        delimiter) or ``"delimiter"`` for a ``DELIMITER`` directive.
    :param value: The verbatim text of the segment, including the
        terminating delimiter of a SQL segment and the trailing line
        break of a directive segment.
    :param start: Character offset (0-based) where the segment starts.
    :param end: Character offset immediately after the segment.
    :param delimiter: For ``"delimiter"`` segments the new statement
        delimiter the directive switches to, otherwise ``None``.
    """

    __slots__ = ()

    @property
    def is_directive(self):
        """True if this segment is a ``DELIMITER`` directive."""
        return self.kind == "delimiter"


class ScriptSplitter:
    """Split a SQL script honouring ``DELIMITER`` directives.

    Feed input incrementally with :meth:`feed` -- it returns every
    segment whose boundary is already certain -- and finish with
    :meth:`close`.  A complete script can be passed to :meth:`run`.

    :param delimiter: Initial statement delimiter (default ``";"``).
    """

    def __init__(self, delimiter=DEFAULT_DELIMITER):
        if not isinstance(delimiter, str) or not delimiter:
            raise ValueError("delimiter must be a non-empty string")
        self.delimiter = delimiter
        self._buffer = ""
        # Position of buffer[0] in the complete input.
        self._base_offset = 0
        self._base_line = 1
        self._base_column = 1
        self._state = _NORMAL
        self._quote = ""
        self._open_pos = 0
        # Index of the opening marker of a carried-over open token.
        self._token_pos = 0
        self._token_len = 0
        # Furthest buffer index already verified as the interior of the
        # carried-over open token (keeps chunked scanning linear).
        self._safe_pos = 0
        # boundary state just before the carried-over token opened.
        self._boundary_before = True
        # The not-yet-emitted prefix of the buffer is all trivia.
        self._at_boundary = True
        self._closed = False

    # -- public API -------------------------------------------------------

    def feed(self, text):
        """Process *text* and return completed :class:`ScriptSegment` items.

        A trailing, possibly incomplete piece is buffered until the next
        call or :meth:`close`.
        """
        if self._closed:
            raise RuntimeError("ScriptSplitter is closed")
        if isinstance(text, TextIOBase):
            text = text.read()
        if not isinstance(text, str):
            raise TypeError(f"Expected text, got {type(text)!r}")
        if not text:
            return []
        self._buffer += text
        segments = []
        self._scan(segments, final=False)
        return segments

    def close(self):
        """Flush buffered input and return the remaining segments.

        Raises :class:`~sqlparse.exceptions.DelimiterError` if the script
        ends with an incomplete ``DELIMITER`` directive.
        """
        segments = []
        if self._buffer:
            self._scan(segments, final=True)
        self._closed = True
        return segments

    def run(self, text):
        """Process the complete *text* and return all segments."""
        segments = self.feed(text)
        segments.extend(self.close())
        return segments

    # -- position handling ------------------------------------------------

    def _advance_base(self, prefix):
        """Drop *prefix* from the front of the buffer, tracking position."""
        self._base_offset += len(prefix)
        for ch in prefix:
            if ch == "\n":
                self._base_line += 1
                self._base_column = 1
            else:
                self._base_column += 1

    def _position(self, index):
        """Return absolute (offset, line, column) for *index* in buffer."""
        line, column = self._base_line, self._base_column
        for ch in self._buffer[:index]:
            if ch == "\n":
                line += 1
                column = 1
            else:
                column += 1
        return self._base_offset + index, line, column

    def _raise(self, message, index):
        from sqlparse.exceptions import DelimiterError

        offset, line, column = self._position(index)
        raise DelimiterError(
            message, offset=offset, line=line, column=column
        )

    # -- scanner ----------------------------------------------------------

    def _remember_interior(self, pos):
        """Remember token-interior text that never needs rescanning."""
        if pos > self._safe_pos:
            self._safe_pos = pos

    def _store(self, buf, state, quote, at_boundary,
               boundary_before=None):
        """Persist scanner state before waiting for more input."""
        self._buffer = buf
        self._state = state
        self._quote = quote
        self._at_boundary = at_boundary
        if boundary_before is not None:
            self._boundary_before = boundary_before

    def _scan(self, segments, final):
        """Scan the buffer and append any completed segments.

        The scanner reprocesses the whole buffer from the start on every
        call.  That is linear overall: every time a segment boundary is
        found the released prefix is dropped from the buffer, so only
        not-yet-decidable trailing text is ever scanned more than once.
        """
        while True:
            buf = self._buffer
            state = self._state
            quote = self._quote
            at_boundary = self._at_boundary
            n = len(buf)
            delimiter = self.delimiter

            # Resume inside an open token carried over from a previous
            # chunk: skip its opening marker (still in the buffer) so the
            # marker is not classified a second time on the rescan.
            if state == _STRING:
                pos = self._open_pos + 1 if final else max(
                    self._open_pos + 1, self._safe_pos
                )
                at_boundary = False
            elif state == _BLOCK_COMMENT:
                # On the final pass an unterminated comment is ordinary
                # SQL, so rescan it from the opening marker (once only).
                pos = self._token_pos + 2 if final else max(
                    self._token_pos + 2, self._safe_pos
                )
                at_boundary = self._boundary_before
            elif state == _LINE_COMMENT:
                if self._token_len == 2:
                    # Pending "--": the next character decides whether it
                    # really starts a comment (MySQL requires whitespace).
                    nxt_index = self._token_pos + 2
                    if nxt_index < n:
                        nxt_ch = buf[nxt_index]
                        if nxt_ch not in _HORIZONTAL_WS:
                            state = _NORMAL
                            pos = self._token_pos
                            at_boundary = self._boundary_before
                        else:
                            pos = (
                                nxt_index + 1
                                if final
                                else max(nxt_index + 1, self._safe_pos)
                            )
                            at_boundary = self._boundary_before
                    elif final:
                        state = _NORMAL
                        pos = self._token_pos
                        at_boundary = self._boundary_before
                    else:
                        self._store(buf, state, quote, at_boundary)
                        return
                else:
                    pos = (
                        self._token_pos + self._token_len
                        if final
                        else max(
                            self._token_pos + self._token_len,
                            self._safe_pos,
                        )
                    )
                    at_boundary = self._boundary_before
            elif state == _DASH:
                pos = self._token_pos
                at_boundary = self._boundary_before
                # The character after the pending '-' decides.
                if pos + 1 >= n:
                    if not final:
                        self._store(buf, state, quote, at_boundary)
                        return
                    # A lone '-' at EOF is an ordinary character.
                    state = _NORMAL
                    self._safe_pos = 0
            elif state == _SLASH:
                pos = self._token_pos
                at_boundary = self._boundary_before
                if pos + 1 < n:
                    if buf[pos + 1] == "*":
                        state = _BLOCK_COMMENT
                        self._token_pos = pos
                        self._token_len = 2
                        self._boundary_before = at_boundary
                        self._safe_pos = pos + 2
                        pos += 2
                    else:
                        state = _NORMAL
                        self._safe_pos = 0
                elif final:
                    state = _NORMAL
                    self._safe_pos = 0
                else:
                    self._store(buf, state, quote, at_boundary)
                    return
            elif state == _STAR:
                pos = self._token_pos
                at_boundary = self._boundary_before
                if pos + 1 < n:
                    if buf[pos + 1] == "/":
                        state = _NORMAL
                        self._safe_pos = 0
                        at_boundary = self._boundary_before
                        pos += 2
                    else:
                        state = _BLOCK_COMMENT
                        self._safe_pos = self._token_pos + 1
                elif final:
                    # Unterminated comment: trailing '*' is content.
                    state = _BLOCK_COMMENT
                else:
                    self._store(buf, state, quote, at_boundary)
                    return
            else:
                pos = 0

            advanced = False
            while pos < n:
                ch = buf[pos]

                if state == _STRING:
                    if ch == "\\" and pos + 1 < n:
                        self._remember_interior(pos + 2)
                        pos += 2
                        continue
                    if ch == quote:
                        if pos + 1 < n and buf[pos + 1] == quote:
                            self._remember_interior(pos + 2)
                            pos += 2
                            continue
                        if pos + 1 < n or not final:
                            state = _NORMAL
                            quote = ""
                            self._safe_pos = 0
                            pos += 1
                            continue
                        # Unterminated quote at EOF: keep the literal.
                    self._remember_interior(pos + 1)
                    pos += 1
                    continue

                if state == _LINE_COMMENT:
                    if ch == "\n":
                        state = _NORMAL
                        self._safe_pos = 0
                        at_boundary = self._boundary_before
                    self._remember_interior(pos + 1)
                    pos += 1
                    continue

                if state == _BLOCK_COMMENT:
                    self._remember_interior(pos + 1)
                    if ch == "*" and pos + 1 < n and buf[pos + 1] == "/":
                        state = _NORMAL
                        self._safe_pos = 0
                        at_boundary = self._boundary_before
                        pos += 2
                        continue
                    if ch == "*" and pos + 1 >= n and not final:
                        self._token_pos = pos
                        self._store(buf, _STAR, quote,
                                    at_boundary,
                                    self._boundary_before)
                        return
                    pos += 1
                    continue

                if state == _STAR:
                    self._remember_interior(pos + 1)
                    if ch == "/":
                        state = _NORMAL
                        self._safe_pos = 0
                        at_boundary = self._boundary_before
                        pos += 1
                        continue
                    # Not a closer: the '*' was ordinary comment text.
                    state = _BLOCK_COMMENT
                    continue

                # --- normal state ------------------------------------
                # The active delimiter takes precedence over comment
                # markers: a multi-character delimiter such as "//" must
                # not be mistaken for the start of a "/*" comment.
                dlen_here = self._delimiter_at(buf, pos, delimiter)
                if dlen_here is not None:
                    end = pos + dlen_here
                    if (
                        not final
                        and end == n
                        and delimiter[0] in _WORD_CHARS
                    ):
                        # A word character in the next chunk could extend
                        # a word-like delimiter into a longer identifier.
                        self._store(buf, state, quote, at_boundary)
                        return
                    self._emit_sql(segments, buf, 0, end)
                    self._advance_base(buf[:end])
                    self._buffer = buf[end:]
                    self._state = _NORMAL
                    self._quote = ""
                    self._safe_pos = 0
                    self._token_pos = 0
                    self._at_boundary = True
                    advanced = True
                    break

                if ch == "-" and not buf.startswith("--", pos):
                    if pos + 1 >= n and not final:
                        # The next chunk could turn this into "--".
                        self._token_pos = pos
                        self._token_len = 1
                        self._store(buf, _DASH, quote, at_boundary,
                                    at_boundary)
                        return
                    at_boundary = False
                    pos += 1
                    continue

                if buf.startswith("--", pos):
                    nxt = buf[pos + 2] if pos + 2 < n else ""
                    if nxt == "" and not final:
                        # Could still become a comment if the next
                        # chunk starts with whitespace.
                        self._token_pos = pos
                        self._token_len = 2
                        self._state = _LINE_COMMENT
                        self._store(buf, _LINE_COMMENT, quote,
                                    at_boundary, at_boundary)
                        return
                    if nxt in _HORIZONTAL_WS:
                        state = _LINE_COMMENT
                        self._token_pos = pos
                        self._token_len = 2
                        self._boundary_before = at_boundary
                        self._safe_pos = pos + 2
                        pos += 2
                        continue
                    at_boundary = False
                    pos += 1
                    continue

                if ch == "#":
                    state = _LINE_COMMENT
                    self._token_pos = pos
                    self._token_len = 1
                    self._boundary_before = at_boundary
                    self._safe_pos = pos + 1
                    pos += 1
                    continue

                if ch == "/" and not buf.startswith("/*", pos):
                    if pos + 1 >= n and not final:
                        # The next chunk could turn this into "/*".
                        self._token_pos = pos
                        self._token_len = 1
                        self._store(buf, _SLASH, quote, at_boundary,
                                    at_boundary)
                        return
                    at_boundary = False
                    pos += 1
                    continue

                if buf.startswith("/*", pos):
                    if pos + 1 >= n:
                        if final:
                            # Unterminated comment at EOF: ordinary text.
                            at_boundary = False
                            pos += 2
                            continue
                        self._store(buf, _BLOCK_COMMENT, quote,
                                    at_boundary, at_boundary)
                        self._token_pos = pos
                        self._token_len = 2
                        return
                    state = _BLOCK_COMMENT
                    self._token_pos = pos
                    self._token_len = 2
                    self._boundary_before = at_boundary
                    self._safe_pos = pos + 2
                    pos += 2
                    continue

                if ch in ("'", '"', "`"):
                    state = _STRING
                    quote = ch
                    self._open_pos = pos
                    self._safe_pos = pos + 1
                    at_boundary = False
                    pos += 1
                    continue

                # DELIMITER directive at a statement boundary?
                if at_boundary and ch in ("d", "D"):
                    result = self._parse_directive(buf, pos, final)
                    tag = result[0]
                    if tag == "wait":
                        self._store(buf, state, quote, at_boundary)
                        return
                    if tag == "directive":
                        _, dir_start, line_end, new_delimiter = result
                        if dir_start > 0:
                            self._emit_sql(segments, buf, 0, dir_start)
                        self._emit_directive(
                            segments, buf, dir_start, line_end,
                            new_delimiter
                        )
                        self.delimiter = new_delimiter
                        self._advance_base(buf[:line_end])
                        self._buffer = buf[line_end:]
                        self._state = _NORMAL
                        self._quote = ""
                        self._safe_pos = 0
                        self._token_pos = 0
                        self._at_boundary = True
                        advanced = True
                        break
                    at_boundary = False
                    pos = result[1]
                    continue

                if ch not in _WS:
                    at_boundary = False
                pos += 1

            if advanced:
                continue

            # Buffer exhausted without releasing anything.
            if final and buf:
                self._emit_sql(segments, buf, 0, len(buf))
                self._advance_base(buf)
                self._buffer = ""
                buf = ""

            self._store(buf, state, quote, at_boundary)
            return

    @staticmethod
    def _delimiter_at(buf, pos, delimiter):
        """Return the delimiter length if it starts at *pos*, else None."""
        if len(delimiter) == 1:
            return 1 if buf[pos] == delimiter else None
        if not buf.startswith(delimiter, pos):
            return None
        if delimiter[0] in _WORD_CHARS:
            before = buf[pos - 1] if pos > 0 else ""
            after = buf[pos + len(delimiter) : pos + len(delimiter) + 1]
            if before in _WORD_CHARS or (
                after and after in _WORD_CHARS
            ):
                return None
        return len(delimiter)

    def _emit_sql(self, segments, buf, start, end):
        value = buf[start:end]
        abs_start = self._base_offset + start
        segments.append(
            ScriptSegment("sql", value, abs_start,
                          abs_start + len(value), None)
        )

    def _emit_directive(self, segments, buf, start, end, new_delimiter):
        value = buf[start:end]
        abs_start = self._base_offset + start
        segments.append(
            ScriptSegment("delimiter", value, abs_start,
                          abs_start + len(value), new_delimiter)
        )

    def _parse_directive(self, buf, pos, final):
        """Inspect a possible ``DELIMITER`` directive at *pos*.

        Returns one of:

        * ``("wait",)`` -- more input needed to decide;
        * ``("word", end)`` -- ordinary identifier-like text;
        * ``("directive", start, end, new_delimiter)``;
        * raises :class:`DelimiterError` for malformed directives.
        """
        n = len(buf)
        match = _DIRECTIVE_RE.match(buf, pos)
        if match is None:
            rest = buf[pos:]
            if final and rest.lower() == "delimiter":
                self._raise("Missing delimiter in DELIMITER directive", pos)
            if (
                not final
                and len(rest) <= 9
                and "delimiter".startswith(rest.lower())
            ):
                return ("wait",)
            word_end = _WORD_RE.match(buf, pos).end()
            return ("word", word_end)

        p = match.end()

        if p >= n:
            if not final:
                return ("wait",)
            self._raise("Missing delimiter in DELIMITER directive", pos)

        if buf[p] not in _WS:
            return ("word", match.end())

        while p < n and buf[p] in _HORIZONTAL_WS:
            p += 1

        if p >= n:
            if not final:
                return ("wait",)
            self._raise("Missing delimiter in DELIMITER directive", pos)

        if buf[p] in _NEWLINES:
            self._raise("Missing delimiter in DELIMITER directive", pos)

        arg_start = p
        while p < n and buf[p] not in _WS:
            p += 1
        arg_end = p

        while p < n and buf[p] in _HORIZONTAL_WS:
            p += 1

        # Only whitespace and comments may follow the argument.
        while True:
            while p < n and buf[p] in _HORIZONTAL_WS:
                p += 1
            if p < n and buf[p] == "#":
                nl = buf.find("\n", p)
                p = n if nl == -1 else nl
                continue
            if p < n and p + 1 >= n and buf[p] == "-":
                if not final:
                    return ("wait",)
            if p + 1 < n and buf.startswith("--", p) and (
                p + 2 >= n or buf[p + 2] in _WS
            ):
                nl = buf.find("\n", p)
                p = n if nl == -1 else nl
                continue
            if buf.startswith("/*", p) or (
                p < n and p + 1 >= n and buf[p] == "/"
            ):
                if p + 1 >= n:
                    if not final:
                        return ("wait",)
                    self._raise(
                        "Unterminated comment in DELIMITER directive", p
                    )
                close = buf.find("*/", p + 2)
                if close == -1:
                    # A block comment may span lines; wait for its
                    # closer unless no more input is expected.
                    if not final:
                        return ("wait",)
                    self._raise(
                        "Unterminated comment in DELIMITER directive", p
                    )
                p = close + 2
                continue
            break

        if p >= n:
            if not final:
                return ("wait",)
            line_end = n
        elif buf[p] in _NEWLINES:
            if buf[p] == "\r" and p + 1 >= n and not final:
                # Could be the start of a CRLF pair in the next chunk.
                return ("wait",)
            newline = _NEWLINE_RE.match(buf, p)
            line_end = newline.end()
        else:
            self._raise(
                "Invalid DELIMITER directive: expected a single "
                "delimiter token",
                p,
            )

        return ("directive", pos, line_end, buf[arg_start:arg_end])

def split_script(sql, encoding=None, delimiter=DEFAULT_DELIMITER):
    """Split *sql* into segments, recognizing ``DELIMITER`` directives.

    Unlike :func:`sqlparse.split`, directives are returned as their own
    segments with :attr:`ScriptSegment.is_directive` set to ``True`` and
    the original text is preserved verbatim: concatenating the segment
    values reproduces the input.

    :param sql: A string (or bytes) containing a SQL script.
    :param encoding: Encoding used when *sql* is bytes (optional).
    :param delimiter: Initial statement delimiter (default ``";"``).
    :returns: A list of :class:`ScriptSegment` instances.
    """
    text = _decode(sql, encoding)
    return ScriptSplitter(delimiter=delimiter).run(text)


def split_script_statements(
    sql, encoding=None, delimiter=DEFAULT_DELIMITER, strip_semicolon=False
):
    """Return only the SQL statements of *sql*, without directive lines.

    :param strip_semicolon: If True, a trailing ``;`` is removed from
        each statement (default False).
    """
    result = []
    for segment in split_script(sql, encoding, delimiter):
        if segment.is_directive:
            continue
        value = segment.value.strip()
        if not value:
            continue
        if strip_semicolon and value.endswith(";"):
            value = value[:-1]
        result.append(value)
    return result


def _decode(text, encoding=None):
    if isinstance(text, TextIOBase):
        text = text.read()
    if isinstance(text, str):
        return text
    if isinstance(text, bytes):
        if encoding:
            return text.decode(encoding)
        try:
            return text.decode("utf-8")
        except UnicodeDecodeError:
            return text.decode("unicode-escape")
    raise TypeError(f"Expected text or file-like object, got {type(text)!r}")
