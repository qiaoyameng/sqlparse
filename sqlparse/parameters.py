"""Conversion between database parameter styles."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlparse import lexer, tokens


QMARK = "qmark"
FORMAT = "format"
NUMERIC = "numeric"
NAMED = "named"
PYFORMAT = "pyformat"

PARAMSTYLES = frozenset({QMARK, FORMAT, NUMERIC, NAMED, PYFORMAT})
POSITIONAL_STYLES = frozenset({QMARK, FORMAT, NUMERIC})

_PYFORMAT_PLACEHOLDER = re.compile(r"%\((?P<name>\w+)\)s", re.UNICODE)
_NUMERIC_PLACEHOLDER = re.compile(r"[:$](?P<number>[0-9]+)$")
_NAMED_PLACEHOLDER = re.compile(r"[:$](?P<name>\w+)$", re.UNICODE)
_WORD = re.compile(r"[^\W\d]\w*", re.UNICODE)
_GENERATED_NUMBER = re.compile(r"^param_([0-9]+)$")


class _Placeholder:
    __slots__ = ("start", "end", "style", "key")

    def __init__(self, start: int, end: int, style: str, key: Any) -> None:
        self.start = start
        self.end = end
        self.style = style
        self.key = key


def _classify_placeholder(value: str, position: int) -> tuple[str, Any]:
    if value == "?":
        return QMARK, None
    if value == "%s":
        return FORMAT, None

    match = _PYFORMAT_PLACEHOLDER.fullmatch(value)
    if match is not None:
        return PYFORMAT, match.group("name")

    match = _NUMERIC_PLACEHOLDER.fullmatch(value)
    if match is not None:
        return NUMERIC, int(match.group("number"))

    match = _NAMED_PLACEHOLDER.fullmatch(value)
    if match is not None:
        return NAMED, match.group("name")

    raise ValueError(
        f"Unsupported parameter placeholder {value!r} at position {position}"
    )


def _iter_tokens(sql: str):
    position = 0
    for ttype, value in lexer.tokenize(sql):
        end = position + len(value)
        yield position, end, ttype, value
        position = end


def _find_placeholders(sql: str) -> list[_Placeholder]:
    placeholders = []
    for start, end, ttype, value in _iter_tokens(sql):
        if ttype is not tokens.Name.Placeholder:
            continue

        if _is_json_question_mark(sql, end, value):
            continue

        style, key = _classify_placeholder(value, start)
        placeholders.append(_Placeholder(start, end, style, key))
    return placeholders


def _is_json_question_mark(sql: str, position: int, value: str) -> bool:
    return value == "?" and position < len(sql) and sql[position] in "|&"


def _existing_names(sql: str) -> set[str]:
    names = set()
    for _, _, ttype, value in _iter_tokens(sql):
        if ttype in tokens.Name and ttype is not tokens.Name.Placeholder:
            names.update(_WORD.findall(value))
        elif ttype is tokens.String.Symbol and len(value) >= 2:
            identifier = value[1:-1].replace('""', '"')
            names.update(_WORD.findall(identifier))
    return names


def _name_factory(reserved: set[str]):
    used_numbers = [
        int(match.group(1))
        for name in reserved
        if (match := _GENERATED_NUMBER.fullmatch(name)) is not None
    ]
    next_number = max(used_numbers, default=0) + 1

    def new_name() -> str:
        nonlocal next_number
        name = f"param_{next_number}"
        next_number += 1
        return name

    return new_name


def _mapping_values(
    placeholders: list[_Placeholder], params: Mapping[str, Any]
) -> tuple[list[Any], list[tuple[str, Any]]]:
    keys = list(dict.fromkeys(item.key for item in placeholders))
    missing = sorted(key for key in keys if key not in params)
    extra = sorted(set(params) - set(keys), key=str)
    if missing:
        raise ValueError(f"Missing parameter values: {', '.join(missing)}")
    if extra:
        raise ValueError(f"Unexpected parameter values: {', '.join(extra)}")

    values = [params[item.key] for item in placeholders]
    unique_values = [(key, params[key]) for key in keys]
    return values, unique_values


def _positional_values(
    placeholders: list[_Placeholder], params: Any
) -> list[Any]:
    if not isinstance(params, Sequence) or isinstance(params, (str, bytes)):
        raise ValueError("Positional parameters require a sequence")
    if len(params) != len(placeholders):
        raise ValueError(
            f"Expected {len(placeholders)} parameter values, "
            f"got {len(params)}"
        )
    return list(params)


def _numeric_values(
    placeholders: list[_Placeholder], params: Any
) -> tuple[list[Any], list[tuple[int, Any]]]:
    numbers = list(dict.fromkeys(item.key for item in placeholders))
    if any(number < 1 for number in numbers):
        raise ValueError("Numeric placeholders must be 1-based")
    if not isinstance(params, Sequence) or isinstance(params, (str, bytes)):
        raise ValueError("Numeric parameters require a sequence")

    referenced = set(numbers)
    missing_nums = sorted(number for number in numbers if number > len(params))
    extra_nums = sorted(
        number
        for number in range(1, len(params) + 1)
        if number not in referenced
    )
    missing = [str(number) for number in missing_nums]
    extra = [str(number) for number in extra_nums]

    if missing:
        raise ValueError(f"Missing parameter values: {', '.join(missing)}")
    if extra:
        raise ValueError(f"Unexpected parameter values: {', '.join(extra)}")

    values = [params[item.key - 1] for item in placeholders]
    unique_values = [(number, params[number - 1]) for number in numbers]
    return values, unique_values


def _replacement(style: str, name: str | None, number: int | None) -> str:
    if style == QMARK:
        return "?"
    if style == FORMAT:
        return "%s"
    if style == NAMED:
        return f":{name}"
    if style == PYFORMAT:
        return f"%({name})s"
    return f":{number}"


def convert_params(
    sql: str,
    params: Any = None,
    to_style: str = QMARK,
    *,
    output_style: str | None = None,
) -> tuple[str, Any]:
    """Convert parameter placeholders and rearrange their values.

    Supported styles are ``qmark`` (``?``), ``format`` (``%s``),
    ``numeric`` (``:1`` or ``$1`` input; ``:1`` output), ``named``
    (``:name`` or ``$name`` input; ``:name`` output), and ``pyformat``
    (``%(name)s``).

    Placeholders are found by tokenizing *sql*. String literals, comments,
    quoted identifiers, casts and other non-placeholder text are copied
    unchanged. Parameter values are never interpolated into the SQL.

    Repeated named references repeat the corresponding value in qmark or
    format output. Numeric output compacts unique references. Missing
    values, extra values and placeholders from incompatible parameter
    styles raise :class:`ValueError`.

    Positional styles return a tuple; named and pyformat return a dictionary.
    """
    if not isinstance(sql, str):
        raise TypeError("sql must be a string")

    if output_style is not None:
        to_style = output_style
    if to_style not in PARAMSTYLES:
        raise ValueError(f"Unsupported target parameter style: {to_style!r}")

    placeholders = _find_placeholders(sql)
    styles = {item.style for item in placeholders}
    if len(styles) > 1:
        found = ", ".join(sorted(styles))
        raise ValueError(f"Cannot mix parameter styles: {found}")

    if not placeholders:
        if params is None or (
            isinstance(params, (Sequence, Mapping))
            and not isinstance(params, (str, bytes))
            and len(params) == 0
        ):
            return sql, () if to_style in POSITIONAL_STYLES else {}
        raise ValueError("Unexpected parameter values for SQL without placeholders")

    source_style = next(iter(styles))

    if source_style == NAMED or source_style == PYFORMAT:
        if not isinstance(params, Mapping):
            raise ValueError(f"{source_style} parameters require a mapping")
        occurrence_values, unique_values = _mapping_values(placeholders, params)
    elif source_style == NUMERIC:
        occurrence_values, unique_values = _numeric_values(placeholders, params)
    else:
        occurrence_values = _positional_values(placeholders, params)
        unique_values = [
            (index, value)
            for index, value in enumerate(occurrence_values, start=1)
        ]

    replacements: list[str] = []
    output_params: Any

    if to_style in (QMARK, FORMAT):
        replacements = [
            _replacement(to_style, None, None) for _ in placeholders
        ]
        output_params = tuple(occurrence_values)
    elif to_style == NUMERIC:
        identifiers = {
            source_key: (None, number)
            for number, (source_key, _) in enumerate(
                unique_values, start=1
            )
        }
        output_params = tuple(value for _, value in unique_values)
        for position, item in enumerate(placeholders, start=1):
            key = position if source_style in (QMARK, FORMAT) else item.key
            _, number = identifiers[key]
            replacements.append(_replacement(to_style, None, number))
    else:
        output_params = {}
        existing_names = _existing_names(sql)
        reserved_names = set(existing_names)
        if source_style in (NAMED, PYFORMAT):
            reserved_names.update(
                source_key for source_key, _ in unique_values
            )
        make_name = _name_factory(reserved_names)
        identifiers = {}
        for source_key, _ in unique_values:
            if (
                to_style in (NAMED, PYFORMAT)
                and source_style in (NAMED, PYFORMAT)
                and source_key not in existing_names
            ):
                target_name = source_key
            else:
                target_name = make_name()
            identifiers[source_key] = (target_name, None)

        for source_key, value in unique_values:
            name, number = identifiers[source_key]
            output_params[name] = value

        for position, item in enumerate(placeholders, start=1):
            if source_style in (QMARK, FORMAT):
                key = position
            else:
                key = item.key
            name, number = identifiers[key]
            replacements.append(_replacement(to_style, name, number))

    pieces = []
    cursor = 0
    for item, replacement in zip(placeholders, replacements):
        pieces.append(sql[cursor:item.start])
        pieces.append(replacement)
        cursor = item.end
    pieces.append(sql[cursor:])

    return "".join(pieces), output_params


convert_paramstyle = convert_params
