#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Analyze bind parameters used by SQL templates.

This module inspects a SQL *template* and reports which bind parameters
an executor would have to supply.  It never interpolates values and never
executes anything.

The five DB-API ``paramstyle`` flavors are supported:

``qmark``
    Question mark style, e.g. ``... WHERE name = ?``
``numeric``
    Numeric, positional style, e.g. ``... WHERE name = :1``
``named``
    Named style, e.g. ``... WHERE name = :name``
``format``
    ANSI-C printf style, e.g. ``... WHERE name = %s``
``pyformat``
    Python extended format style, e.g. ``... WHERE name = %(name)s``

The style can be given explicitly or detected automatically.  When the
style cannot be determined unambiguously the result keeps the ambiguity
instead of guessing a parameter list that might mislead the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlparse import tokens as T
from sqlparse.exceptions import SQLParseError
from sqlparse.lexer import Lexer

__all__ = [
    "ParamStyle",
    "ParamOccurrence",
    "Parameter",
    "ParameterList",
    "ParameterStyleConflict",
    "extract_parameters",
]

# Markers that are not one of the five supported DB-API styles.
DOLLAR = "dollar"


class ParamStyle(str, Enum):
    """Supported bind parameter styles (DB-API ``paramstyle`` values)."""

    QMARK = "qmark"
    NUMERIC = "numeric"
    NAMED = "named"
    FORMAT = "format"
    PYFORMAT = "pyformat"


class ParameterStyleConflict(SQLParseError):
    """Raised when an explicit style conflicts with markers found in SQL.

    Attributes:
        style: The explicitly requested parameter style.
        conflicting_kinds: Marker kinds that don't fit *style*.
    """

    def __init__(self, style, conflicting_kinds):
        self.style = style
        self.conflicting_kinds = tuple(conflicting_kinds)
        kinds = ", ".join(self.conflicting_kinds)
        super().__init__(
            f"SQL template mixes bind parameter styles: expected "
            f"{style.value} but also found: {kinds}"
        )


@dataclass(frozen=True)
class ParamOccurrence:
    """A single occurrence of a bind parameter in the template.

    Attributes:
        value: The marker text as it appears in the SQL, e.g. ``:name``
            or ``?``.
        start: Zero-based character offset of the marker in the SQL.
        end: Zero-based character offset just past the marker.
        line: One-based line number of the marker.
        column: One-based column number of the marker.
        number: For ``numeric`` style, the positional number (``1`` for
            ``:1``); otherwise ``None``.
    """

    value: str
    start: int
    end: int
    line: int
    column: int
    number: int | None = None


@dataclass(frozen=True)
class Parameter:
    """A distinct bind parameter with all of its occurrences.

    A named parameter that is reused several times is reported once with
    several occurrences.

    Attributes:
        name: Parameter name for ``named`` and ``pyformat`` styles,
            otherwise ``None``.
        position: One-based position of the parameter, in order of first
            appearance.
        occurrences: Every occurrence of the parameter.
    """

    name: str | None
    position: int
    occurrences: tuple[ParamOccurrence, ...]

    @property
    def count(self) -> int:
        """Number of times the parameter is used in the template."""
        return len(self.occurrences)


@dataclass(frozen=True)
class ParameterList:
    """Bind parameters found in a SQL template.

    Attributes:
        style: The style the parameters are reported in.  In automatic
            mode this is ``None`` whenever the style is ambiguous or no
            markers were found.
        parameters: Distinct parameters in order of first appearance.
            Empty when *ambiguous* is ``True``.
        ambiguous: ``True`` when automatic detection cannot determine a
            single style.
        detected_styles: Every marker kind found, in order of first
            appearance.  Supported styles use their DB-API names; other
            markers (e.g. PostgreSQL native ``$1``) appear as ``"dollar"``.
        candidates: One parameter tuple per supported style that could
            apply.  Populated only when *ambiguous* is ``True``.
    """

    style: ParamStyle | None
    parameters: tuple[Parameter, ...]
    ambiguous: bool = False
    detected_styles: tuple[str, ...] = ()
    candidates: dict[str, tuple[Parameter, ...]] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        """Names of the distinct parameters in order of first appearance."""
        return tuple(p.name for p in self.parameters if p.name is not None)

    def __iter__(self):
        return iter(self.parameters)

    def __len__(self):
        return len(self.parameters)

    def __getitem__(self, index):
        return self.parameters[index]


def _classify(value):
    """Return the marker kind of a placeholder token value.

    Returns one of the :class:`ParamStyle` values, ``"dollar"`` for
    PostgreSQL native placeholders like ``$1``, or ``None`` for tokens
    that are not bind parameters.
    """
    if value.startswith("?"):
        return ParamStyle.QMARK
    if value.startswith("%("):
        return ParamStyle.PYFORMAT
    if value.startswith("%"):
        return ParamStyle.FORMAT
    if value.startswith("$"):
        return DOLLAR
    if value.startswith(":"):
        if value[1:].isdigit():
            return ParamStyle.NUMERIC
        return ParamStyle.NAMED
    return None


def _scan_markers(sql, encoding=None):
    """Tokenize *sql* and yield marker tuples.

    Yields ``(kind, value, start, end, line, column)`` for every bind
    parameter marker.

    The lexer already hides the contents of string literals (single,
    double quoted, backtick quoted and dollar quoted), quoted
    identifiers and comments, so markers found there are never reported.
    ``%%`` escapes and bare ``%`` modulo operators are not placeholders
    and are skipped as well.
    """
    offset = 0
    line = 1
    line_start = 0
    for ttype, value in Lexer.get_default_instance().get_tokens(sql, encoding):
        start = offset
        end = offset + len(value)
        if ttype == T.Name.Placeholder:
            kind = _classify(value)
            if kind is not None:
                column = start - line_start + 1
                yield kind, value, start, end, line, column
        count = value.count("\n")
        if count:
            line += count
            line_start = start + value.rfind("\n") + 1
        offset = end


def _marker_identity(style, marker):
    """Return ``(name, number)`` identifying the parameter of *marker*."""
    kind, value = marker[0], marker[1]
    if kind == ParamStyle.NAMED:
        return value[1:], None
    if kind == ParamStyle.PYFORMAT:
        return value[2:value.rindex(")")], None
    if kind == ParamStyle.NUMERIC:
        return None, int(value[1:])
    return None, None


def _build_parameters(style, markers):
    """Group raw *markers* of *style* into distinct :class:`Parameter`
    objects, ordered by first appearance.

    ``named``, ``pyformat`` and ``numeric`` markers with the same name
    or number describe one reused parameter.  Positional styles
    (``qmark`` and ``format``) treat every occurrence as distinct.
    """
    reuse = style in (ParamStyle.NAMED, ParamStyle.PYFORMAT, ParamStyle.NUMERIC)
    groups: dict = {}
    order: list = []
    for occurrence_index, marker in enumerate(markers):
        _, value, start, end, line, column = marker
        name, number = _marker_identity(style, marker)
        occurrence = ParamOccurrence(
            value=value,
            start=start,
            end=end,
            line=line,
            column=column,
            number=number if style == ParamStyle.NUMERIC else None,
        )
        key = (name, number) if reuse else occurrence_index
        if key not in groups:
            groups[key] = {"name": name, "occurrences": []}
            order.append(key)
        groups[key]["occurrences"].append(occurrence)
    return tuple(
        Parameter(
            name=groups[key]["name"],
            position=position,
            occurrences=tuple(groups[key]["occurrences"]),
        )
        for position, key in enumerate(order, start=1)
    )


def extract_parameters(sql, style=None, encoding=None):
    """List the bind parameters used in a SQL template.

    :param sql: A string containing one or more SQL statements.
    :param style: Explicit parameter style as a :class:`ParamStyle`
        (or one of the strings ``"qmark"``, ``"numeric"``, ``"named"``,
        ``"format"``, ``"pyformat"``).  When omitted the style is
        detected automatically.
    :param encoding: The encoding of *sql* if it is given as bytes
        (optional).
    :returns: A :class:`ParameterList`.
    :raises ParameterStyleConflict: If *style* is given but the template
        contains markers of another style.

    The template is analyzed only; no values are interpolated and the
    SQL is never executed.

    In automatic mode the result is marked ambiguous when markers of
    more than one style are present or the only markers found are not
    one of the five supported styles (e.g. PostgreSQL native ``$1``).
    In that case ``parameters`` is empty and ``candidates`` holds the
    interpretations the caller must choose from.
    """
    markers = list(_scan_markers(sql, encoding))

    if style is not None:
        if not isinstance(style, ParamStyle):
            try:
                style = ParamStyle(style)
            except ValueError as exc:
                raise ValueError(f"Unknown parameter style: {style!r}") from exc
        foreign = []
        for marker in markers:
            if marker[0] != style:
                label = marker[0].value if isinstance(marker[0], ParamStyle) else DOLLAR
                if label not in foreign:
                    foreign.append(label)
        if foreign:
            raise ParameterStyleConflict(style, foreign)
        return ParameterList(
            style=style,
            parameters=_build_parameters(style, markers),
            detected_styles=tuple(_detected_kinds(markers)),
        )

    kinds = _detected_kinds(markers)
    if len(kinds) == 1 and isinstance(markers[0][0], ParamStyle):
        only_style = markers[0][0]
        return ParameterList(
            style=only_style,
            parameters=_build_parameters(only_style, markers),
            detected_styles=(only_style.value,),
        )

    if not kinds:
        return ParameterList(style=None, parameters=())

    candidates = {}
    for candidate in ParamStyle:
        matching = [marker for marker in markers if marker[0] == candidate]
        if matching:
            candidates[candidate.value] = _build_parameters(candidate, matching)
    return ParameterList(
        style=None,
        parameters=(),
        ambiguous=True,
        detected_styles=tuple(kinds),
        candidates=candidates,
    )


def _detected_kinds(markers):
    """Marker kind labels in order of first appearance."""
    kinds = []
    for marker in markers:
        kind = marker[0]
        label = kind.value if isinstance(kind, ParamStyle) else kind
        if label not in kinds:
            kinds.append(label)
    return kinds
