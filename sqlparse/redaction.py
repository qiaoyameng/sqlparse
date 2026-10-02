"""Redact literals from SQL intended for diagnostic logs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from sqlparse import keywords, lexer, tokens as T

HintPolicy = Literal["mask", "preserve", "strip"]

DEFAULT_STRING_MARKER = "[REDACTED]"
DEFAULT_NUMBER_MARKER = "[REDACTED_NUMBER]"
DEFAULT_BOOLEAN_MARKER = "[REDACTED_BOOLEAN]"
DEFAULT_BIND_MARKER = ":BIND"
DEFAULT_IN_LIST_MARKER = "[REDACTED_VALUES]"
DEFAULT_UNKNOWN_MARKER = "[REDACTED_UNPARSED]"
DEFAULT_HINT_MASK = "/* [REDACTED_HINT] */"
SHAPE_STRING_MARKER = "?"
SHAPE_NUMBER_MARKER = "?"
SHAPE_BOOLEAN_MARKER = "?"
SHAPE_IN_LIST_MARKER = "?"
SHAPE_UNKNOWN_MARKER = "<?>"
SHAPE_PREFIX = "sqlshape-"
SHAPE_DIGEST_LENGTH = 16

_BOOLEAN_KEYWORDS = frozenset(("TRUE", "FALSE"))
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RedactionOptions:
    """Options controlling literal and comment redaction."""

    string_marker: str = DEFAULT_STRING_MARKER
    number_marker: str = DEFAULT_NUMBER_MARKER
    boolean_marker: str = DEFAULT_BOOLEAN_MARKER
    in_list_marker: str = DEFAULT_IN_LIST_MARKER
    unknown_marker: str = DEFAULT_UNKNOWN_MARKER
    hint_mask: str = DEFAULT_HINT_MASK
    in_list_threshold: int | None = 5
    mask_booleans: bool = True
    hint_policy: HintPolicy = "strip"


@dataclass(frozen=True)
class RedactedSQL:
    """Diagnostic SQL text and a stable query-shape identifier."""

    text: str
    shape: str
    shape_id: str


def _decode_text(value: str | bytes, encoding: str | None) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        if encoding is not None:
            return value.decode(encoding)
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1")
    raise TypeError(f"Expected text or bytes, got {type(value)!r}")


def _is_string(ttype: object) -> bool:
    return ttype in T.String


def _is_number(ttype: object) -> bool:
    return ttype in T.Literal and ttype in T.Number


def _is_placeholder(ttype: object) -> bool:
    return ttype in T.Name.Placeholder


def _is_comment(ttype: object) -> bool:
    return ttype in T.Comment


def _is_hint(ttype: object) -> bool:
    return _is_comment(ttype) and "Hint" in str(ttype)


def _is_whitespace(ttype: object) -> bool:
    return ttype in T.Whitespace


def _is_boolean(ttype: object, value: str) -> bool:
    return ttype in T.Keyword and value.upper() in _BOOLEAN_KEYWORDS


def _skip_whitespace(tokens: list[tuple[object, str]], index: int,
                     end: int) -> int:
    while index < end and _is_whitespace(tokens[index][0]):
        index += 1
    return index


_INTERVAL_EXTENSIONS = frozenset((
    "DAY", "HOUR", "MINUTE", "MONTH", "SECOND", "YEAR"))


def _analyze_in_contents(tokens: list[tuple[object, str]], start: int,
                         end: int,
                         mask_booleans: bool) -> tuple[bool, int]:
    item_count = 1
    pos = _skip_whitespace(tokens, start, end)

    while pos < end:
        if tokens[pos][0] is T.Punctuation and tokens[pos][1] == "(":
            return False, item_count

        ttype, value = tokens[pos]
        is_typed_literal = ttype is T.Name.Builtin
        is_interval = is_typed_literal and value.upper() == "INTERVAL"
        if is_typed_literal:
            pos = _skip_whitespace(tokens, pos + 1, end)
            if pos >= end:
                return False, item_count
            ttype, value = tokens[pos]

        is_literal = (
            _is_string(ttype)
            or _is_number(ttype)
            or _is_placeholder(ttype)
            or ttype is T.Literal
            or (mask_booleans and _is_boolean(ttype, value))
        )
        if is_typed_literal:
            allows_number = is_interval
            if not (
                _is_string(ttype)
                or ttype is T.Literal
                or (allows_number and _is_number(ttype))
            ):
                return False, item_count
        elif not is_literal:
            return False, item_count
        pos += 1

        if is_interval:
            pos = _skip_whitespace(tokens, pos, end)
            if (pos >= end
                    or tokens[pos][0] not in T.Keyword
                    or tokens[pos][1].upper() not in _INTERVAL_EXTENSIONS):
                return False, item_count
            pos += 1

        pos = _skip_whitespace(tokens, pos, end)
        if pos == end:
            return True, item_count
        if tokens[pos][0] is not T.Punctuation or tokens[pos][1] != ",":
            return False, item_count

        pos = _skip_whitespace(tokens, pos + 1, end)
        if pos >= end:
            return False, item_count
        item_count += 1


def _find_collapsed_in_lists(tokens: list[tuple[object, str]],
                             threshold: int | None,
                             mask_booleans: bool) -> set[int]:
    if threshold is None:
        return set()

    collapsed: set[int] = set()
    index = 0
    while index < len(tokens):
        ttype, value = tokens[index]
        if ttype is T.Keyword and value.upper() != "IN":
            index += 1
            continue

        open_pos = _skip_whitespace(tokens, index + 1, len(tokens))
        if not (open_pos < len(tokens)
                and tokens[open_pos][0] is T.Punctuation
                and tokens[open_pos][1] == "("):
            index += 1
            continue

        close_pos = open_pos + 1
        parenthesis_depth = 1
        while close_pos < len(tokens):
            close_type, close_value = tokens[close_pos]
            if close_type is T.Punctuation and close_value == "(":
                parenthesis_depth += 1
            elif close_type is T.Punctuation and close_value == ")":
                parenthesis_depth -= 1
                if parenthesis_depth == 0:
                    break
            close_pos += 1
        if close_pos == len(tokens):
            return collapsed

        valid, item_count = _analyze_in_contents(
            tokens, open_pos + 1, close_pos,
            mask_booleans)
        if valid and item_count >= threshold:
            collapsed.update(range(open_pos, close_pos + 1))
        index = close_pos + 1
    return collapsed


def _unclosed_delimited_ranges(
        text: str, token_positions: set[int]) -> list[tuple[int, int]]:
    spans = keywords.find_delimited_spans(text)
    return [(pos, len(text)) for pos, (_end, _tag, ttype)
            in spans.openers.items()
            if pos in token_positions
            if spans.resolve(pos) is None
            and (ttype in T.Comment or ttype in T.Literal)]


def _unclosed_quote_ranges(text: str, tokens: list[tuple[object, str]]
                           ) -> list[tuple[int, int]]:
    ranges = []
    pos = 0
    for ttype, value in tokens:
        if ttype is T.Error and value in {"'", '"', "`"}:
            ranges.append((pos, len(text)))
        pos += len(value)
    return ranges


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged = [sorted(ranges)[0]]
    for start, end in sorted(ranges)[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _mask_range(parts: list[tuple[int, str]], start: int, end: int,
                marker: str) -> None:
    offset = 0
    emitted = False
    for index, (length, _value) in enumerate(parts):
        next_offset = offset + length
        if offset < end and next_offset > start:
            parts[index] = (length, "" if emitted else marker)
            emitted = True
        offset = next_offset


def _normalize_shape(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _shape_identifier(shape: str) -> str:
    digest = hashlib.sha256(shape.encode("utf-8")).hexdigest()
    return SHAPE_PREFIX + digest[:SHAPE_DIGEST_LENGTH]


def _redact_tzcast(value: str, marker: str) -> str:
    return re.sub(r"'[^']*'", f"'{marker}'", value)


def _redact_statement(statement_text: str,
                      options: RedactionOptions) -> tuple[str, str]:
    tokens = list(lexer.tokenize(statement_text))
    token_positions: set[int] = set()
    token_offset = 0
    for _ttype, value in tokens:
        token_positions.add(token_offset)
        token_offset += len(value)
    collapsed = _find_collapsed_in_lists(
        tokens, options.in_list_threshold, options.mask_booleans)
    unknown_ranges = _merge_ranges(
        _unclosed_delimited_ranges(statement_text, token_positions)
        + _unclosed_quote_ranges(statement_text, tokens))

    display_parts: list[tuple[int, str]] = []
    shape_parts: list[tuple[int, str]] = []
    first_collapsed = min(collapsed, default=-1)
    last_collapsed = max(collapsed, default=-1)

    for index, (ttype, value) in enumerate(tokens):
        if index in collapsed:
            if index == first_collapsed:
                length = sum(len(tokens[pos][1])
                             for pos in range(first_collapsed,
                                             last_collapsed + 1))
                display_parts.append(
                    (length, f"({options.in_list_marker})"))
                shape_parts.append(
                    (length, f"({SHAPE_IN_LIST_MARKER})"))
            continue

        if _is_comment(ttype):
            if _is_hint(ttype):
                if options.hint_policy == "preserve":
                    display_parts.append((len(value), value))
                elif options.hint_policy == "mask":
                    display_parts.append((len(value), options.hint_mask))
                else:
                    display_parts.append((len(value), ""))
            else:
                display_parts.append((len(value), ""))
            shape_parts.append((len(value), ""))
            continue

        if ttype is T.Keyword.TZCast:
            display_value = _redact_tzcast(value, options.string_marker)
            shape_value = _redact_tzcast(
                value.upper(), SHAPE_STRING_MARKER)
            display_parts.append((len(value), display_value))
            shape_parts.append((len(value), shape_value))
            continue

        display_value = value
        shape_value = value.upper() if ttype in T.Keyword else value

        if _is_number(ttype):
            display_value = options.number_marker
            shape_value = SHAPE_NUMBER_MARKER
        elif _is_string(ttype) or ttype is T.Literal:
            display_value = options.string_marker
            shape_value = SHAPE_STRING_MARKER
        elif options.mask_booleans and _is_boolean(ttype, value):
            display_value = options.boolean_marker
            shape_value = SHAPE_BOOLEAN_MARKER
        elif _is_placeholder(ttype):
            display_value = value
            shape_value = DEFAULT_BIND_MARKER
        elif ttype is T.Error:
            display_value = options.unknown_marker
            shape_value = SHAPE_UNKNOWN_MARKER
        display_parts.append((len(value), display_value))
        shape_parts.append((len(value), shape_value))

    for start, end in unknown_ranges:
        _mask_range(display_parts, start, end, options.unknown_marker)
        _mask_range(shape_parts, start, end, SHAPE_UNKNOWN_MARKER)

    return ("".join(value for _length, value in display_parts),
            _normalize_shape("".join(value for _length, value
                                     in shape_parts)))


def _build_options(options: RedactionOptions | None,
                   overrides: dict[str, object]) -> RedactionOptions:
    if options is not None:
        custom = {name: value for name, value in overrides.items()
                  if value != getattr(RedactionOptions(), name)}
        if custom:
            raise ValueError("Do not pass both options and option overrides")
        return options
    return RedactionOptions(**overrides)


def redact(sql_text: str | bytes, encoding: str | None = None, *,
           options: RedactionOptions | None = None,
           string_marker: str = DEFAULT_STRING_MARKER,
           number_marker: str = DEFAULT_NUMBER_MARKER,
           boolean_marker: str = DEFAULT_BOOLEAN_MARKER,
           in_list_marker: str = DEFAULT_IN_LIST_MARKER,
           unknown_marker: str = DEFAULT_UNKNOWN_MARKER,
           hint_mask: str = DEFAULT_HINT_MASK,
           in_list_threshold: int | None = 5,
           mask_booleans: bool = True,
           hint_policy: HintPolicy = "strip") -> RedactedSQL:
    """Redact literals and return diagnostic text plus a stable shape ID."""

    text = _decode_text(sql_text, encoding)
    redaction_options = _build_options(options, {
        "string_marker": string_marker,
        "number_marker": number_marker,
        "boolean_marker": boolean_marker,
        "in_list_marker": in_list_marker,
        "unknown_marker": unknown_marker,
        "hint_mask": hint_mask,
        "in_list_threshold": in_list_threshold,
        "mask_booleans": mask_booleans,
        "hint_policy": hint_policy,
    })
    if redaction_options.hint_policy not in {"strip", "mask", "preserve"}:
        raise ValueError("hint_policy must be 'strip', 'mask', or 'preserve'")
    if redaction_options.in_list_threshold is not None and (
            not isinstance(redaction_options.in_list_threshold, int)
            or redaction_options.in_list_threshold < 1):
        raise ValueError("in_list_threshold must be a positive integer or None")

    display, shape = _redact_statement(text, redaction_options)
    return RedactedSQL(display, shape, _shape_identifier(shape))
