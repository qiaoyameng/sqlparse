#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""SQL formatter"""

import re

from sqlparse import engine
from sqlparse import filters
from sqlparse import lexer
from sqlparse import tokens as T
from sqlparse.exceptions import SQLParseError


_TOGGLE_RE = re.compile(r'sqlparse\s*:\s*(off|on)', re.IGNORECASE)


def validate_options(options):
    """Validates options."""
    kwcase = options.get('keyword_case')
    if kwcase not in [None, 'upper', 'lower', 'capitalize']:
        raise SQLParseError('Invalid value for keyword_case: '
                            f'{kwcase!r}')

    idcase = options.get('identifier_case')
    if idcase not in [None, 'upper', 'lower', 'capitalize']:
        raise SQLParseError('Invalid value for identifier_case: '
                            f'{idcase!r}')

    ofrmt = options.get('output_format')
    if ofrmt not in [None, 'sql', 'python', 'php']:
        raise SQLParseError('Unknown output format: '
                            f'{ofrmt!r}')

    strip_comments = options.get('strip_comments', False)
    if strip_comments not in [True, False]:
        raise SQLParseError('Invalid value for strip_comments: '
                            f'{strip_comments!r}')

    space_around_operators = options.get('use_space_around_operators', False)
    if space_around_operators not in [True, False]:
        raise SQLParseError('Invalid value for use_space_around_operators: '
                            f'{space_around_operators!r}')

    strip_ws = options.get('strip_whitespace', False)
    if strip_ws not in [True, False]:
        raise SQLParseError('Invalid value for strip_whitespace: '
                            f'{strip_ws!r}')

    truncate_strings = options.get('truncate_strings')
    if truncate_strings is not None:
        try:
            truncate_strings = int(truncate_strings)
        except (ValueError, TypeError):
            raise SQLParseError('Invalid value for truncate_strings: '
                                f'{truncate_strings!r}')
        if truncate_strings <= 1:
            raise SQLParseError('Invalid value for truncate_strings: '
                                f'{truncate_strings!r}')
        options['truncate_strings'] = truncate_strings
        options['truncate_char'] = options.get('truncate_char', '[...]')

    indent_columns = options.get('indent_columns', False)
    if indent_columns not in [True, False]:
        raise SQLParseError('Invalid value for indent_columns: '
                            f'{indent_columns!r}')
    elif indent_columns:
        options['reindent'] = True  # enforce reindent
    options['indent_columns'] = indent_columns

    reindent = options.get('reindent', False)
    if reindent not in [True, False]:
        raise SQLParseError('Invalid value for reindent: '
                            f'{reindent!r}')
    elif reindent:
        options['strip_whitespace'] = True

    reindent_aligned = options.get('reindent_aligned', False)
    if reindent_aligned not in [True, False]:
        raise SQLParseError('Invalid value for reindent_aligned: '
                            f'{reindent!r}')
    elif reindent_aligned:
        options['strip_whitespace'] = True

    indent_after_first = options.get('indent_after_first', False)
    if indent_after_first not in [True, False]:
        raise SQLParseError('Invalid value for indent_after_first: '
                            f'{indent_after_first!r}')
    options['indent_after_first'] = indent_after_first

    indent_tabs = options.get('indent_tabs', False)
    if indent_tabs not in [True, False]:
        raise SQLParseError('Invalid value for indent_tabs: '
                            f'{indent_tabs!r}')
    elif indent_tabs:
        options['indent_char'] = '\t'
    else:
        options['indent_char'] = ' '

    indent_width = options.get('indent_width', 2)
    try:
        indent_width = int(indent_width)
    except (TypeError, ValueError):
        raise SQLParseError('indent_width requires an integer')
    if indent_width < 1:
        raise SQLParseError('indent_width requires a positive integer')
    options['indent_width'] = indent_width

    wrap_after = options.get('wrap_after', 0)
    try:
        wrap_after = int(wrap_after)
    except (TypeError, ValueError):
        raise SQLParseError('wrap_after requires an integer')
    if wrap_after < 0:
        raise SQLParseError('wrap_after requires a positive integer')
    options['wrap_after'] = wrap_after

    comma_first = options.get('comma_first', False)
    if comma_first not in [True, False]:
        raise SQLParseError('comma_first requires a boolean value')
    options['comma_first'] = comma_first

    format_toggle = options.get('format_toggle', True)
    if format_toggle not in [True, False]:
        raise SQLParseError('format_toggle requires a boolean value')
    options['format_toggle'] = format_toggle

    compact = options.get('compact', False)
    if compact not in [True, False]:
        raise SQLParseError('compact requires a boolean value')
    options['compact'] = compact

    right_margin = options.get('right_margin')
    if right_margin is not None:
        try:
            right_margin = int(right_margin)
        except (TypeError, ValueError):
            raise SQLParseError('right_margin requires an integer')
        if right_margin < 10:
            raise SQLParseError('right_margin requires an integer > 10')
    options['right_margin'] = right_margin

    return options


def build_filter_stack(stack, options):
    """Setup and return a filter stack.

    Args:
      stack: :class:`~sqlparse.filters.FilterStack` instance
      options: Dictionary with options validated by validate_options.
    """
    # Token filter
    if options.get('keyword_case'):
        stack.preprocess.append(
            filters.KeywordCaseFilter(options['keyword_case']))

    if options.get('identifier_case'):
        stack.preprocess.append(
            filters.IdentifierCaseFilter(options['identifier_case']))

    if options.get('truncate_strings'):
        stack.preprocess.append(filters.TruncateStringFilter(
            width=options['truncate_strings'], char=options['truncate_char']))

    if options.get('use_space_around_operators', False):
        stack.enable_grouping()
        stack.stmtprocess.append(filters.SpacesAroundOperatorsFilter())

    # After grouping
    if options.get('strip_comments'):
        stack.enable_grouping()
        stack.stmtprocess.append(filters.StripCommentsFilter())

    if options.get('strip_whitespace') or options.get('reindent'):
        stack.enable_grouping()
        stack.stmtprocess.append(filters.StripWhitespaceFilter())

    if options.get('reindent'):
        stack.enable_grouping()
        stack.stmtprocess.append(
            filters.ReindentFilter(
                char=options['indent_char'],
                width=options['indent_width'],
                indent_after_first=options['indent_after_first'],
                indent_columns=options['indent_columns'],
                wrap_after=options['wrap_after'],
                comma_first=options['comma_first'],
                compact=options['compact'],))

    if options.get('reindent_aligned', False):
        stack.enable_grouping()
        stack.stmtprocess.append(
            filters.AlignedIndentFilter(char=options['indent_char']))

    if options.get('right_margin'):
        stack.enable_grouping()
        stack.stmtprocess.append(
            filters.RightMarginFilter(width=options['right_margin']))

    # Serializer
    if options.get('output_format'):
        frmt = options['output_format']
        if frmt.lower() == 'php':
            fltr = filters.OutputPHPFilter()
        elif frmt.lower() == 'python':
            fltr = filters.OutputPythonFilter()
        else:
            fltr = None
        if fltr is not None:
            stack.postprocess.append(fltr)

    return stack


def _toggle_action(ttype, value):
    """Return 'off' or 'on' if the token is a format toggle comment.

    Only standalone comments whose whole body is the directive are
    recognized. The same text inside a string literal is never seen here
    because the lexer doesn't tokenize it as a comment.
    """
    if ttype not in (T.Comment.Single, T.Comment.Multiline):
        return None
    body = value.strip()
    if body.startswith('--'):
        body = body[2:]
    elif body.startswith('#'):
        body = body[1:]
    elif body.startswith('/*') and body.endswith('*/'):
        body = body[2:-2]
    else:
        return None
    match = _TOGGLE_RE.fullmatch(body.strip())
    return match.group(1).lower() if match else None


def _line_start(text, pos):
    """Return the start position of a toggle comment's protected region.

    Leading spaces and tabs of the comment's line as well as the preceding
    newline are included so that the comment keeps its line position when
    the surrounding SQL is reformatted.
    """
    while pos > 0 and text[pos - 1] in ' \t':
        pos -= 1
    if pos > 0 and text[pos - 1] == '\n':
        pos -= 1
        if pos > 0 and text[pos - 1] == '\r':
            pos -= 1
    return pos


def find_protected_spans(text):
    """Return character spans of *text* protected by format toggle comments.

    A protected span starts at the beginning of the line of an
    ``sqlparse: off`` comment and ends right after the matching
    ``sqlparse: on`` comment. The following rules apply:

    * toggles are only recognized in standalone comments, the same text in
      a string literal has no effect;
    * regions don't nest: an ``off`` toggle inside a protected region is
      plain text and the first ``on`` toggle ends the region;
    * an ``on`` toggle without a preceding ``off`` toggle is a plain
      comment;
    * a region that isn't closed protects everything up to the end of the
      input;
    * regions may span multiple statements.
    """
    if 'sqlparse' not in text.lower():
        return []
    spans = []
    pos = 0
    start = None
    for ttype, value in lexer.tokenize(text):
        token_start, pos = pos, pos + len(value)
        action = _toggle_action(ttype, value)
        if start is None:
            if action == 'off':
                # Never overlap with the previous region, which may own the
                # newline this region would otherwise extend into.
                start = max(_line_start(text, token_start),
                            spans[-1][1] if spans else 0)
        elif action == 'on':
            spans.append((start, pos))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _format_segment(segment, options):
    # Each segment is formatted with a fresh filter stack so that stateful
    # filters (e.g. ReindentFilter tracks the previous statement) don't
    # leak state across protected regions.
    stack = build_filter_stack(engine.FilterStack(), options)
    stack.postprocess.append(filters.SerializerUnicode())
    return ''.join(stack.run(segment))


def format_with_toggles(text, spans, options):
    """Format *text* while keeping the given *spans* byte-for-byte intact.

    *options* must be validated by :func:`validate_options`.
    """
    chunks = []
    last_char = ''

    def append(chunk, formattable):
        nonlocal last_char
        if formattable:
            chunk = _format_segment(chunk, options)
        if not chunk:
            return
        if last_char and not last_char.isspace() and not chunk[0].isspace():
            # Formatting may have stripped the whitespace that separated
            # the last token from the protected region. Insert a space so
            # that two tokens are never glued together at the boundary.
            chunks.append(' ')
        chunks.append(chunk)
        last_char = chunk[-1]

    prev = 0
    for start, end in spans:
        append(text[prev:start], True)
        append(text[start:end], False)
        prev = end
    append(text[prev:], True)
    return ''.join(chunks)
