#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

import pytest

import sqlparse
from sqlparse.exceptions import SQLParseError

ALIGNED = (
    '-- sqlparse: off\n'
    'select   employee_id,\n'
    '         first_name\n'
    '\n'
    '\t-- keep\tme\n'
    'from     hr.employees;\n'
    '-- sqlparse: on\n'
)


def test_region_preserved_verbatim():
    sql = 'select 1;\n' + ALIGNED + 'select 2;\n'
    out = sqlparse.format(sql, keyword_case='upper', reindent=True)
    assert ALIGNED in out


def test_region_preserves_blank_lines_tabs_and_comments():
    out = sqlparse.format(ALIGNED, reindent=True, strip_comments=True)
    assert '\n\n' in out
    assert '\t-- keep\tme\n' in out


def test_formatting_applies_outside_region():
    sql = 'select a,\n       b\n' + ALIGNED + 'select 2;\n'
    out = sqlparse.format(sql, keyword_case='upper', reindent=True)
    head, _, tail = out.partition(ALIGNED)
    assert 'SELECT a,' in head
    assert 'SELECT 2;' in tail


def test_toggle_in_string_is_ignored():
    sql = "select '-- sqlparse: off',  x"
    out = sqlparse.format(sql, keyword_case='upper', strip_whitespace=True)
    assert out == "SELECT '-- sqlparse: off', x"


def test_block_comment_toggles():
    sql = 'select 1;\n/* sqlparse: off */  a   =  1  /* sqlparse: on */\nselect 2;'
    out = sqlparse.format(sql, keyword_case='upper', strip_whitespace=True)
    assert '/* sqlparse: off */  a   =  1  /* sqlparse: on */' in out
    assert 'SELECT 2;' in out


def test_toggle_directive_is_case_insensitive():
    sql = '-- SQLPARSE: OFF\n  x\n-- SqlParse: ON\nselect  1;'
    out = sqlparse.format(sql, strip_whitespace=True)
    assert '\n  x\n' in out
    assert out.endswith('select 1;')


def test_toggle_comment_with_extra_text_is_ignored():
    sql = '-- sqlparse: off because reasons\nselect   1;'
    out = sqlparse.format(sql, strip_whitespace=True)
    assert 'select 1;' in out


def test_unpaired_off_protects_to_end_of_input():
    sql = 'select 1;\n-- sqlparse: off\nselect   2;'
    out = sqlparse.format(sql, strip_whitespace=True)
    assert out.endswith('select   2;')


def test_lone_on_is_a_plain_comment():
    sql = '-- sqlparse: on\nselect   1;'
    out = sqlparse.format(sql, strip_whitespace=True)
    assert 'select 1;' in out


def test_nested_off_is_ignored_first_on_closes():
    sql = ('-- sqlparse: off\na   =  1\n-- sqlparse: off\nb  =  2\n'
           '-- sqlparse: on\nc   =   3')
    out = sqlparse.format(sql, strip_whitespace=True)
    assert 'a   =  1' in out
    assert 'b  =  2' in out
    assert out.endswith('c = 3')


def test_region_may_span_statements():
    sql = ('-- sqlparse: off\nselect  1;\nselect   2;\n-- sqlparse: on\n'
           'select   3;')
    out = sqlparse.format(sql, strip_whitespace=True)
    assert 'select  1;\nselect   2;' in out
    assert out.endswith('select 3;')


def test_boundary_does_not_glue_tokens():
    # The minus must not be swallowed by the following line comment.
    sql = 'SELECT a - -- sqlparse: off\n x\n-- sqlparse: on\n;'
    out = sqlparse.format(sql, reindent=True)
    assert '- -- sqlparse: off' in out
    # A comment directly after a token gets separated by a space.
    sql = 'SELECT 1;-- sqlparse: off\n x\n-- sqlparse: on\nSELECT 2;'
    out = sqlparse.format(sql, reindent=True)
    assert 'SELECT 1; -- sqlparse: off' in out


def test_format_twice_is_stable():
    sql = ('select a,\n       b\nfrom t1;\n' + ALIGNED +
           'SELECT 1;-- sqlparse: off\n x  =  1\n-- sqlparse: on\nselect 2;')
    once = sqlparse.format(sql, keyword_case='upper', reindent=True)
    twice = sqlparse.format(once, keyword_case='upper', reindent=True)
    assert once == twice
    assert ALIGNED in twice


def test_format_toggle_disabled_keeps_legacy_behavior():
    sql = '-- sqlparse: off\nselect   1;\n-- sqlparse: on\n'
    # With the feature disabled the toggle comments are plain comments and
    # the SQL between them is formatted like anything else.
    assert sqlparse.format(sql, strip_whitespace=True, format_toggle=False) == \
        '-- sqlparse: off\nselect 1;-- sqlparse: on\n'
    # The same output as for ordinary comments of different content.
    ctrl = sql.replace('sqlparse', 'ctrl')
    assert sqlparse.format(sql, strip_whitespace=True, format_toggle=False) == \
        sqlparse.format(ctrl, strip_whitespace=True).replace('ctrl', 'sqlparse')


def test_format_toggle_invalid_option():
    with pytest.raises(SQLParseError):
        sqlparse.format('select 1', format_toggle='yes')
