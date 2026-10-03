#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Exceptions used in this package."""


class SQLParseError(Exception):
    """Base class for exceptions in this module."""


class DelimiterError(SQLParseError):
    """Raised for an invalid or unterminated ``DELIMITER`` directive.

    The position attributes are 1-based and point at the first character
    of the offending directive (the leading ``D`` of ``DELIMITER``) or at
    the offending token within the directive line.

    :param message: Human readable error description.
    :param offset: Character offset (0-based) in the complete input.
    :param line: Line number (1-based).
    :param column: Column number (1-based).
    """

    def __init__(self, message, offset=None, line=None, column=None):
        self.offset = offset
        self.line = line
        self.column = column
        location = ""
        if line is not None and column is not None:
            location = f" (line {line}, column {column})"
        elif offset is not None:
            location = f" (offset {offset})"
        super().__init__(f"{message}{location}")
