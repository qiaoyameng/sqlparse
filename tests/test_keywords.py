import re

import pytest

from sqlparse import tokens
from sqlparse.lexer import LexerConfigBuilder


class TestSQLREGEX:
    @pytest.mark.parametrize('number', ['1.0', '-1.0',
                                        '1.', '-1.',
                                        '.1', '-.1'])
    def test_float_numbers(self, number):
        sql_regex = LexerConfigBuilder.default().build()._sql_regex
        ttype = next(
            ttype
            for pattern, ttype in sql_regex
            if re.compile(pattern, re.IGNORECASE | re.UNICODE).match(number)
        )
        assert tokens.Number.Float == ttype
