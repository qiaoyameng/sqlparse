#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Inspect the bind parameters a SQL template expects.

This module analyzes a statement without interpolating or executing it.
It recognizes the five DB-API 2.0 paramstyles: ``qmark`` (``?``),
``numeric`` (``:1`` or ``$1``), ``named`` (``:name`` or ``$name``),
``format`` (``%s``) and ``pyformat`` (``%(name)s``).

Placeholders inside string literals, quoted identifiers, dollar-quoted
literals and comments are ignored, as are PostgreSQL ``::`` casts and
``%%`` percent escapes.
"""

import re
from typing import NamedTuple

from sqlparse import exceptions, lexer
from sqlparse import tokens as T

#: Parameter styles recognized by :func:`find`.
PARAM_STYLES = ("qmark", "numeric", "named", "format", "pyformat")

# `?` is also a PostgreSQL JSON operator (`?`, `?|`, `?&`); there is no
# reliable way to tell the operator apart from a qmark placeholder without
# full syntax analysis, so only the compounded forms are excluded here.
_QMARK_RE = re.compile(r"\?(?![|&])")
_NUMERIC_RE = re.compile(r"(?<![\w:]):(\d+)|(?<![\w$])\$(\d+)")
_NAMED_RE = re.compile(r"(?<![\w:]):([^\W\d]\w*)|(?<![\w$])\$([^\W\d]\w*)")
# A run of percent signs: pairs are `%%` escapes, so a placeholder only
# exists when the run is odd, and it starts at the last `%` of the run.
_FORMAT_RE = re.compile(r"(?<!%)%+(?:\((\w+)\))?s")

# Quoted identifiers lexed as T.Name (double-quoted ones are T.Literal).
_QUOTED_NAME_PREFIXES = ("`", "´", "[")


class Parameter(NamedTuple):
    """A single bind parameter expected by a SQL template.

    ``name`` is set for ``named`` and ``pyformat`` styles, ``position``
    (1-based) for ``qmark``, ``format`` and ``numeric`` styles.
    ``spans`` holds the ``(start, end)`` offsets of every occurrence in
    the original statement and ``count`` the number of occurrences.
    """

    name: str | None
    position: int | None
    count: int
    spans: tuple[tuple[int, int], ...]


class ParameterReport(NamedTuple):
    """Result of :func:`find`.

    ``style`` is the resolved parameter style or ``None`` when it could
    not be determined unambiguously. ``styles`` lists every style that
    was detected, in order of first appearance. ``parameters`` is empty
    when the report is ambiguous, so that callers never act on a guessed
    parameter list.
    """

    style: str | None
    parameters: tuple[Parameter, ...]
    styles: tuple[str, ...]

    @property
    def ambiguous(self) -> bool:
        """True when more than one parameter style was detected."""
        return len(self.styles) > 1


class _Occurrence(NamedTuple):
    start: int
    end: int
    style: str
    name: str | None
    position: int | None


def _mask_non_code(tokens_, text):
    """Blank out comments, literals and quoted identifiers in *text*."""
    chars = list(text)
    offset = 0
    for ttype, value in tokens_:
        end = offset + len(value)
        if (
            ttype in T.Comment
            or ttype in T.Literal
            or (ttype is T.Name and value.startswith(_QUOTED_NAME_PREFIXES))
        ):
            for idx in range(offset, end):
                if chars[idx] not in "\r\n":
                    chars[idx] = " "
        offset = end
    return "".join(chars)


def _scan(tokens_, text):
    """Find all placeholder occurrences in the statement."""
    masked = _mask_non_code(tokens_, text)
    occurrences = []
    for match in _QMARK_RE.finditer(masked):
        occurrences.append(
            _Occurrence(match.start(), match.end(), "qmark", None, None))
    for match in _NUMERIC_RE.finditer(masked):
        number = match.group(1) or match.group(2)
        occurrences.append(
            _Occurrence(match.start(), match.end(), "numeric",
                        None, int(number)))
    for match in _NAMED_RE.finditer(masked):
        name = match.group(1) or match.group(2)
        occurrences.append(
            _Occurrence(match.start(), match.end(), "named", name, None))
    for match in _FORMAT_RE.finditer(masked):
        run = len(match.group()) - len(match.group().lstrip("%"))
        if run % 2 == 0:
            continue  # escaped percent signs, e.g. `%%s`
        name = match.group(1)
        style = "pyformat" if name is not None else "format"
        occurrences.append(
            _Occurrence(match.start() + run - 1, match.end(),
                        style, name, None))
    occurrences.sort()
    return occurrences


def _group(occurrences, style):
    """Collapse occurrences into parameters for the given style."""
    if style in ("qmark", "format"):
        return tuple(
            Parameter(None, index, 1, ((occ.start, occ.end),))
            for index, occ in enumerate(occurrences, 1))
    grouped = {}
    for occ in occurrences:
        key = occ.name if style in ("named", "pyformat") else occ.position
        entry = grouped.setdefault(
            key, Parameter(occ.name, occ.position, 0, ()))
        grouped[key] = entry._replace(
            count=entry.count + 1,
            spans=entry.spans + ((occ.start, occ.end),))
    return tuple(grouped.values())


def find(sql, style=None, encoding=None):
    """Return a :class:`ParameterReport` for the given SQL template.

    :param sql: A string (or file-like object) with one or more SQL
        statements. The statement is only analyzed, never interpolated
        or executed.
    :param style: One of :data:`PARAM_STYLES` to declare the expected
        parameter style, or ``None`` to detect it automatically.
    :param encoding: The encoding of the statement (optional).
    :raises ParameterStyleConflict: In explicit mode, when the statement
        contains placeholders of a different style than requested.
    :returns: A :class:`ParameterReport`. In automatic mode the report is
        ambiguous (``style`` is ``None`` and ``parameters`` is empty)
        whenever more than one style is detected.
    """
    if style is not None and style not in PARAM_STYLES:
        raise ValueError(
            f"unknown parameter style: {style!r} "
            f"(expected one of {', '.join(PARAM_STYLES)})")
    token_list = list(lexer.tokenize(sql, encoding))
    text = "".join(value for _, value in token_list)
    occurrences = _scan(token_list, text)
    detected = tuple(dict.fromkeys(occ.style for occ in occurrences))

    if style is not None:
        for occ in occurrences:
            if occ.style != style:
                raise exceptions.ParameterStyleConflict(
                    f"found {occ.style} placeholder "
                    f"{text[occ.start:occ.end]!r} at offset {occ.start}, "
                    f"but parameter style {style!r} was requested")
        return ParameterReport(style, _group(occurrences, style), detected)

    if len(detected) == 1:
        return ParameterReport(
            detected[0], _group(occurrences, detected[0]), detected)
    # Zero or several styles detected: don't guess a parameter list that
    # could mislead the executing side, keep the ambiguity instead.
    return ParameterReport(None, (), detected)
