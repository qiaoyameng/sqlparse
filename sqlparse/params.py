"""Explicit conversion between SQL parameter placeholder styles.

The helpers in this module rewrite placeholder tokens in SQL text and
return the new SQL together with the reordered parameter values.  Values
are never interpolated into the SQL string; the input is only tokenized,
so placeholders inside string literals, comments, quoted identifiers or
casts (``::type``) are left untouched and all other text is preserved
byte-for-byte.

Recognized placeholder styles:

- positional: ``?`` (qmark) and ``%s`` (format)
- named: ``:name``, ``$name`` and ``%(name)s`` (pyformat)

Conversion rules:

- Mixing positional and named placeholders in one input raises
  :class:`~sqlparse.exceptions.ParameterStyleError`.
- Named placeholders require a mapping of values; positional
  placeholders require a sequence of values.  Any other combination
  raises :class:`~sqlparse.exceptions.ParameterStyleError`.
- A repeated named placeholder repeats its value in the output.
- Missing values and unused (extra) values raise
  :class:`~sqlparse.exceptions.ParameterStyleError`.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sqlparse import tokens as T
from sqlparse.exceptions import ParameterStyleError
from sqlparse.lexer import Lexer

__all__ = ["to_qmark", "to_named"]

# Matches named placeholders anywhere in the raw text, including inside
# strings or comments, so that generated names never collide with them.
_EXISTING_NAMES_REGEX = re.compile(r"[:$](\w+)|%\((\w+)\)s")

_PREFIX_REGEX = re.compile(r"[A-Za-z_]\w*\Z")


def _placeholder_name(value: str) -> str | None:
    """Return the name of a named placeholder, None for positional."""
    if value in ("?", "%s"):
        return None
    if value.startswith("%(") and value.endswith(")s"):
        return value[2:-2]
    return value[1:]  # ":name" or "$name"


def to_qmark(
    sql: str, params: Mapping[str, Any] | Sequence[Any]
) -> tuple[str, tuple[Any, ...]]:
    """Convert placeholders in *sql* to qmark (``?``) style.

    :param sql: A string containing one or more SQL statements.
    :param params: A mapping of values for named placeholders or a
        sequence of values for positional placeholders.
    :returns: A tuple ``(new_sql, values)`` where *values* is a tuple
        aligned with the ``?`` placeholders in *new_sql*.
    :raises sqlparse.exceptions.ParameterStyleError: If the statement
        mixes placeholder styles, if *params* does not match the
        placeholder style, or if values are missing or unused.
    """
    tokens_, names = _tokenize(sql)
    _validate(names, params)
    if any(name is not None for name in names):
        values = tuple(params[name] for name in names)  # type: ignore[index]
    else:
        values = tuple(params)  # type: ignore[arg-type]
    return _rebuild(tokens_, iter("?" for _ in names)), values


def to_named(
    sql: str,
    params: Mapping[str, Any] | Sequence[Any],
    prefix: str = "p",
) -> tuple[str, dict[str, Any]]:
    """Convert placeholders in *sql* to named (``:name``) style.

    Positional placeholders are replaced by generated names built from
    *prefix* and a counter (``p1``, ``p2``, ...).  Generated names never
    collide with names already present in *sql*.  Existing named
    placeholders keep their names and are normalized to ``:name`` form.

    :param sql: A string containing one or more SQL statements.
    :param params: A mapping of values for named placeholders or a
        sequence of values for positional placeholders.
    :param prefix: Prefix for generated parameter names.
    :returns: A tuple ``(new_sql, values)`` where *values* is a dict
        mapping parameter names to values.
    :raises sqlparse.exceptions.ParameterStyleError: If the statement
        mixes placeholder styles, if *params* does not match the
        placeholder style, or if values are missing or unused.
    :raises ValueError: If *prefix* is not a valid identifier start.
    """
    if not _PREFIX_REGEX.match(prefix):
        raise ValueError(f"Invalid parameter name prefix: {prefix!r}")
    tokens_, names = _tokenize(sql)
    _validate(names, params)
    if any(name is not None for name in names):
        values = {
            name: params[name]  # type: ignore[index]
            for name in dict.fromkeys(names)
            if name is not None
        }
        replacements = [":" + name for name in names]  # type: ignore[misc]
    else:
        reserved = {
            name
            for match in _EXISTING_NAMES_REGEX.finditer(sql)
            for name in match.groups()
            if name
        }
        generated: list[str] = []
        counter = 0
        for _ in names:
            counter += 1
            while f"{prefix}{counter}" in reserved:
                counter += 1
            generated.append(f"{prefix}{counter}")
        values = dict(zip(generated, params))
        replacements = [":" + name for name in generated]
    return _rebuild(tokens_, iter(replacements)), values


def _tokenize(sql: str) -> tuple[list[tuple[Any, str]], list[str | None]]:
    """Tokenize *sql* and return the tokens and placeholder names."""
    lexer = Lexer.get_default_instance()
    tokens_ = list(lexer.get_tokens(sql))
    names = [
        _placeholder_name(value)
        for ttype, value in tokens_
        if ttype is T.Name.Placeholder
    ]
    return tokens_, names


def _validate(
    names: list[str | None], params: Mapping[str, Any] | Sequence[Any]
) -> None:
    """Check that *params* matches the placeholders found in the SQL."""
    has_positional = any(name is None for name in names)
    has_named = any(name is not None for name in names)
    if has_positional and has_named:
        raise ParameterStyleError(
            "Cannot convert a statement that mixes positional and named "
            "placeholders."
        )
    if not names:
        if len(params) != 0:
            raise ParameterStyleError(
                "Statement has no placeholders but parameter values "
                "were given."
            )
        return
    if has_named:
        if not isinstance(params, Mapping):
            raise ParameterStyleError(
                "Named placeholders require a mapping of parameter values."
            )
        used = {name for name in names if name is not None}
        missing = used - params.keys()
        if missing:
            raise ParameterStyleError(
                "Missing values for parameter(s): "
                + ", ".join(sorted(repr(name) for name in missing))
            )
        extra = params.keys() - used
        if extra:
            raise ParameterStyleError(
                "Unused values for parameter(s): "
                + ", ".join(sorted(repr(name) for name in extra))
            )
    else:
        if isinstance(params, Mapping) or isinstance(params, (str, bytes)):
            raise ParameterStyleError(
                "Positional placeholders require a sequence of "
                "parameter values."
            )
        if len(params) != len(names):
            raise ParameterStyleError(
                f"Expected {len(names)} parameter value(s), "
                f"got {len(params)}."
            )


def _rebuild(
    tokens_: list[tuple[Any, str]], replacements: Iterator[str]
) -> str:
    """Rebuild SQL from tokens, substituting placeholder replacements."""
    return "".join(
        next(replacements) if ttype is T.Name.Placeholder else value
        for ttype, value in tokens_
    )
