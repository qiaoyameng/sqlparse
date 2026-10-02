#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Redaction of SQL text for diagnostics.

This module provides :func:`redact`, a standalone entry point aimed at
slow query logs and similar diagnostics pipelines.  It turns raw SQL
text into two related outputs:

* display text with sensitive literal values masked, and
* a stable *shape identifier* that groups statements which only differ
  in literal values, so that aggregates per query shape are possible
  without keeping customer data around.

Both outputs are diagnostic text.  They are not meant to be executed.
"""

import hashlib
import re
from dataclasses import dataclass

from sqlparse import lexer
from sqlparse import tokens as T

#: Notice attached to every redaction result: the outputs are diagnostic
#: text, not executable SQL.
DIAGNOSTIC_NOTICE = (
    'Diagnostic text for query analysis; masked output is not '
    'executable SQL.'
)

# Canonical markers used in the shape representation.  None of them
# contains input text, so grouping is stable and secrets cannot leak
# into the shape identifier -- not even indirectly via comments or
# optimizer hints that are kept in the display text.
_SHAPE_LITERAL = '?'
_SHAPE_PLACEHOLDER = ':param'
_SHAPE_HINT = '/*+*/'
_SHAPE_COMMENT = '/**/'
_SHAPE_UNRECOGNIZED = '<unrecognized>'

# Keywords that are literal values rather than syntax.
_BOOLEAN_LITERALS = ('TRUE', 'FALSE')

#: Keyword tokens (e.g. ``AT TIME ZONE 'UTC'``) may embed a quoted
#: literal; it is masked like any other string literal.
_QUOTED_IN_KEYWORD = re.compile(r"'[^']*'")

_COMMENT_POLICIES = ('remove', 'mask')
_HINT_POLICIES = ('keep', 'mask', 'remove')


@dataclass(frozen=True)
class RedactPolicy:
    """Configurable masking rules for :func:`redact`.

    :param string_mask: Replacement for string literals, including
        dollar-quoted literals.
    :param number_mask: Replacement for numeric literals.
    :param boolean_mask: Replacement for the boolean literals
        ``TRUE`` and ``FALSE``.
    :param placeholder_mask: Replacement for bound parameters
        (``?``, ``%s``, ``:name``, ``$1``, ...).  ``None`` keeps the
        original placeholder text in the display output.  Bound
        parameters are always kept distinct from literal values.
    :param in_list_threshold: ``IN`` lists with at least this many
        literal items are collapsed into ``in_list_mask``.  ``None``
        disables collapsing.
    :param in_list_mask: Replacement for a collapsed ``IN`` list,
        including the parentheses.
    :param comment_policy: How to treat regular comments:
        ``'remove'`` (default) or ``'mask'``.
    :param comment_mask: Replacement for comments when
        ``comment_policy='mask'``.
    :param hint_policy: How to treat optimizer hints (``/*+ ... */``,
        ``--+ ...``): ``'keep'``, ``'mask'`` (default) or ``'remove'``.
        Kept hints appear in the display text only; the shape
        identifier never contains hint text.
    :param hint_mask: Replacement for hints when ``hint_policy='mask'``.
    :param error_mask: Conservative replacement for fragments the
        lexer could not recognize.
    """

    string_mask: str = '?'
    number_mask: str = '?'
    boolean_mask: str = '?'
    placeholder_mask: str | None = None
    in_list_threshold: int | None = 5
    in_list_mask: str = '(?)'
    comment_policy: str = 'remove'
    comment_mask: str = '/*REDACTED*/'
    hint_policy: str = 'mask'
    hint_mask: str = '/*+REDACTED*/'
    error_mask: str = _SHAPE_UNRECOGNIZED

    def __post_init__(self):
        if self.comment_policy not in _COMMENT_POLICIES:
            raise ValueError(
                f'comment_policy must be one of {_COMMENT_POLICIES}')
        if self.hint_policy not in _HINT_POLICIES:
            raise ValueError(
                f'hint_policy must be one of {_HINT_POLICIES}')
        if (self.in_list_threshold is not None
                and self.in_list_threshold < 2):
            raise ValueError('in_list_threshold must be None or >= 2')


@dataclass(frozen=True)
class RedactionResult:
    """Result of :func:`redact`.

    ``text`` is the masked display text, ``shape`` the normalized
    representation used for grouping and ``fingerprint`` the stable
    shape identifier (SHA-256 of ``shape``).  Statements that only
    differ in literal values share the same ``shape`` and
    ``fingerprint``.
    """

    text: str
    shape: str
    fingerprint: str
    is_diagnostic: bool = True
    notice: str = DIAGNOSTIC_NOTICE


def _mask_token(ttype, value, policy):
    """Return a ``(display, shape)`` pair for a single non-whitespace
    token, or ``None`` if the token must be dropped entirely."""
    if ttype in (T.Comment.Single.Hint, T.Comment.Multiline.Hint):
        # Optimizer hints are handled by an explicit policy.  Even a
        # kept hint only contributes a fixed marker to the shape, so
        # hint text can never leak into the shape identifier.
        if policy.hint_policy == 'keep':
            return value, _SHAPE_HINT
        if policy.hint_policy == 'mask':
            return policy.hint_mask, _SHAPE_HINT
        return None
    if ttype in T.Comment:
        if policy.comment_policy == 'mask':
            return policy.comment_mask, _SHAPE_COMMENT
        return None
    if ttype is T.Name.Placeholder:
        # Bound parameters carry no literal value; they are normalized
        # to their own shape marker, distinct from masked literals.
        if policy.placeholder_mask is None:
            return value, _SHAPE_PLACEHOLDER
        return policy.placeholder_mask, _SHAPE_PLACEHOLDER
    if ttype in T.Number:
        return policy.number_mask, _SHAPE_LITERAL
    if ttype in T.String.Symbol:
        # Quoted identifier, not a literal: names are part of the
        # statement shape.
        return value, value
    if ttype in T.String or ttype is T.Literal:
        # String literals, including dollar-quoted literals.
        return policy.string_mask, _SHAPE_LITERAL
    if ttype is T.Keyword and value.upper() in _BOOLEAN_LITERALS:
        return policy.boolean_mask, _SHAPE_LITERAL
    if ttype is T.Error:
        # Unrecognized fragment: mask conservatively.
        return policy.error_mask, _SHAPE_UNRECOGNIZED
    if ttype in T.Keyword:
        if "'" in value:
            # Keyword tokens with an embedded quoted literal
            # (e.g. AT TIME ZONE 'UTC').
            display = _QUOTED_IN_KEYWORD.sub(policy.string_mask, value)
            shape = _QUOTED_IN_KEYWORD.sub(_SHAPE_LITERAL, value.upper())
            return display, shape
        return value, value.upper()
    return value, value


def _in_list_item_count(inner):
    """Return the number of items if `inner` (the token stream between
    the parentheses of an ``IN`` list) consists only of literals, bound
    parameters, booleans/NULL and commas; ``None`` otherwise."""
    count = 1
    seen_item = False
    for ttype, value in inner:
        if ttype in T.Whitespace or ttype in T.Comment:
            continue
        if (ttype in T.Number or ttype in T.String
                or ttype is T.Literal or ttype is T.Name.Placeholder):
            seen_item = True
        elif (ttype is T.Keyword
              and value.upper() in _BOOLEAN_LITERALS + ('NULL',)):
            seen_item = True
        elif ttype is T.Punctuation and value == ',':
            count += 1
        else:
            return None
    return count if seen_item else None


def _match_in_list(toklist, pos, threshold):
    """If the ``IN`` keyword at `pos` is followed by a parenthesized
    literal list with at least `threshold` items, return the index
    right behind the closing parenthesis.  Otherwise return ``None``."""
    idx = pos + 1
    while idx < len(toklist) and toklist[idx][0] in T.Whitespace:
        idx += 1
    if idx >= len(toklist) or toklist[idx] != (T.Punctuation, '('):
        return None
    depth = 0
    end = idx
    while end < len(toklist):
        ttype, value = toklist[end]
        if ttype is T.Punctuation:
            if value == '(':
                depth += 1
            elif value == ')':
                depth -= 1
                if depth == 0:
                    break
        end += 1
    else:
        return None  # unbalanced parentheses
    count = _in_list_item_count(toklist[idx + 1:end])
    if count is None or count < threshold:
        return None
    return end + 1


def redact(sql, encoding=None, policy=None):
    """Mask sensitive literals in `sql` and compute a stable shape
    identifier for aggregation.

    :param sql: A string containing one or more SQL statements.
    :param encoding: The encoding of the statement (optional).
    :param policy: A :class:`RedactPolicy` with the masking rules
        (optional, defaults are conservative).
    :returns: A :class:`RedactionResult` with diagnostic text only;
        the masked output is not executable SQL.
    """
    policy = policy if policy is not None else RedactPolicy()
    toklist = list(lexer.tokenize(sql, encoding))

    display_parts = []
    shape_parts = []
    pending_space = False

    def emit(display, shape):
        nonlocal pending_space
        if pending_space and display_parts:
            display_parts.append(' ')
            shape_parts.append(' ')
        pending_space = False
        display_parts.append(display)
        shape_parts.append(shape)

    idx = 0
    while idx < len(toklist):
        ttype, value = toklist[idx]
        if ttype in T.Whitespace:
            pending_space = True
        elif (policy.in_list_threshold is not None
              and ttype is T.Keyword and value.upper() == 'IN'
              and (end := _match_in_list(
                  toklist, idx, policy.in_list_threshold)) is not None):
            # Large literal IN lists are collapsed as a whole.
            emit(*_mask_token(ttype, value, policy))
            pending_space = True
            emit(policy.in_list_mask, policy.in_list_mask)
            idx = end
            continue
        else:
            masked = _mask_token(ttype, value, policy)
            if masked is None:
                # Dropped token: keep the neighbors separated.
                pending_space = True
            else:
                emit(*masked)
        idx += 1

    text = ''.join(display_parts)
    shape = ''.join(shape_parts)
    fingerprint = hashlib.sha256(shape.encode('utf-8')).hexdigest()
    return RedactionResult(text=text, shape=shape, fingerprint=fingerprint)
