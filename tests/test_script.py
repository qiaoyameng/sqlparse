# Tests for the explicit script-splitting mode (DELIMITER directives).

import pytest

import sqlparse
from sqlparse.exceptions import SQLParseError
from sqlparse.script import (
    DelimiterDirective,
    ScriptSplitter,
    SqlStatement,
    split,
    split_iter,
)


SCRIPT = """\
-- header
DELIMITER $$
CREATE PROCEDURE p1()
BEGIN
  SELECT 'semicolon; inside' AS x;
  SELECT `tick``;delim` AS y;
  -- $$ inside a line comment
  /* ; $$ DELIMITER inside a block comment */
  SELECT "double;quote";
END$$

CREATE PROCEDURE p2()
BEGIN
  SELECT 2;
END $$

DELIMITER //
SELECT 'slash' AS a//
CALL p1()//
DELIMITER ;

-- trailing query
SELECT * FROM t;
SELECT * FROM u
"""


def _items(script, **kw):
    return split(script, strip=False, **kw)


def _key(items):
    return [
        (
            type(item).__name__,
            item.value,
            item.offset,
            item.line,
            item.column,
            item.new_delimiter if item.is_directive else item.delimiter,
        )
        for item in items
    ]


def test_basic_two_procedures():
    items = _items(SCRIPT)
    directives = [i for i in items if i.is_directive]
    statements = [i for i in items if not i.is_directive]
    assert len(directives) == 3
    assert [d.new_delimiter for d in directives] == ['$$', '//', ';']
    assert len(statements) == 6
    # Semicolons inside procedure bodies stay part of the statement.
    assert statements[0].delimiter == '$$'
    assert "SELECT 'semicolon; inside' AS x;" in statements[0].value
    assert statements[0].value.rstrip().endswith('END$$')
    assert statements[1].value.rstrip().endswith('END $$')
    assert statements[2].value.rstrip().endswith('AS a//')
    assert statements[2].delimiter == '//'
    assert statements[3].value.rstrip().endswith('CALL p1()//')
    assert statements[4].value.strip() == \
        '-- trailing query\nSELECT * FROM t;'
    # Trailing statement has no terminator.
    assert statements[5].delimiter is None
    assert statements[5].value.strip() == 'SELECT * FROM u'


def test_lossless_reconstruction():
    assert ''.join(i.value for i in _items(SCRIPT)) == SCRIPT


def test_directives_distinguished_from_sql():
    items = _items(SCRIPT)
    kinds = [type(i) for i in items]
    assert kinds.count(DelimiterDirective) == 3
    assert kinds.count(SqlStatement) == 6
    assert all(isinstance(i, DelimiterDirective) == i.is_directive
               for i in items)


@pytest.mark.parametrize('size', [1, 2, 3, 4, 5, 7, 13, 64, 1000])
def test_chunked_input_matches_whole_input(size):
    splitter = ScriptSplitter()
    chunked = []
    for start in range(0, len(SCRIPT), size):
        chunked.extend(splitter.feed(SCRIPT[start:start + size]))
    chunked.extend(splitter.close())
    assert _key(chunked) == _key(_items(SCRIPT))


def test_single_character_feed():
    whole = _items(SCRIPT)
    splitter = ScriptSplitter()
    streamed = []
    for ch in SCRIPT:
        streamed.extend(splitter.feed(ch))
    streamed.extend(splitter.close())
    assert _key(streamed) == _key(whole)


def test_split_iter_is_lazy_generator():
    gen = split_iter(SCRIPT)
    first = next(gen)
    from sqlparse.script import ScriptItem
    assert isinstance(first, ScriptItem)
    rest = list(gen)
    assert len(rest) + 1 == 9


def test_custom_initial_delimiter():
    items = _items('a@@b@@', delimiter='@@')
    assert len(items) == 2
    assert items[0].value == 'a@@'
    assert items[1].value == 'b@@'
    assert items[1].delimiter == '@@'


def test_repeated_switches_and_multichar():
    items = _items('a;b;DELIMITER @@\nc@@d@@\nDELIMITER xx\nef xx')
    assert [i.value.strip() for i in items if not i.is_directive] == \
        ['a;', 'b;', 'c@@', 'd@@', 'ef xx']
    assert [i.new_delimiter for i in items if i.is_directive] == \
        ['@@', 'xx']


def test_directive_not_recognized_inside_body():
    # At a non-boundary position DELIMITER is an ordinary identifier.
    items = _items('SELECT delimiter FROM t;')
    assert len(items) == 1
    assert not items[0].is_directive


@pytest.mark.parametrize('sql', [
    "SELECT 'DELIMITER $$'; SELECT 2;",
    'SELECT "DELIMITER $$";',
    'SELECT `DELIMITER` FROM t;',
    '-- DELIMITER $$\nSELECT 1;',
    '# DELIMITER $$\nSELECT 1;',
    '/* DELIMITER $$ */ SELECT 1;',
    "SELECT ';'; SELECT '$$';",
])
def test_directive_and_delimiter_inert_in_strings_and_comments(sql):
    items = _items(sql)
    assert not any(i.is_directive for i in items)
    # Delimiters inside literals must not terminate statements early.
    assert ''.join(i.value for i in items) == sql


def test_directive_case_insensitive():
    items = _items('delimiter $$\na$$\nDeLiMiTeR ;\nb;')
    assert [i.new_delimiter for i in items if i.is_directive] == ['$$', ';']


def test_directive_word_boundary():
    # A word merely starting with "delimiter" is not a directive.
    items = _items('SELECT delimiterx FROM t;')
    assert len(items) == 1
    assert not items[0].is_directive


def test_trailing_whitespace_in_argument_stripped():
    items = _items('DELIMITER  @@   \na@@')
    assert items[0].is_directive
    assert items[0].new_delimiter == '@@'


def test_internal_spaces_in_argument_kept():
    # The mysql client keeps internal spaces of the delimiter argument.
    items = _items('DELIMITER @@ X\na@@ X')
    assert items[0].new_delimiter == '@@ X'


def test_directive_value_preserved():
    items = _items('  -- lead\n\tDELIMITER $$\n')
    directive = items[0]
    assert directive.is_directive
    assert '-- lead' in directive.value
    assert 'DELIMITER $$' in directive.value


@pytest.mark.parametrize('script,line,column', [
    ('DELIMITER', 1, 1),
    ('DELIMITER\n', 1, 1),
    ('SELECT 1;\nDELIMITER', 2, 1),
    ('SELECT 1;\n\nDELIMITER   \n', 3, 1),
])
def test_invalid_directive_raises_locatable_error(script, line, column):
    with pytest.raises(SQLParseError) as exc:
        _items(script)
    message = str(exc.value)
    assert f'line {line}' in message
    assert f'column {column}' in message
    assert 'offset' in message


def test_chunked_invalid_directive_error_position():
    splitter = ScriptSplitter()
    with pytest.raises(SQLParseError) as whole_exc:
        _items('SELECT 1;\nDELIMITER')
    splitter = ScriptSplitter()
    with pytest.raises(SQLParseError) as chunk_exc:
        for ch in 'SELECT 1;\nDELIMITER':
            splitter.feed(ch)
        splitter.close()
    assert str(chunk_exc.value) == str(whole_exc.value)


def test_locations():
    items = _items('SELECT 1;\nDELIMITER $$\nx$$')
    directive = [i for i in items if i.is_directive][0]
    assert (directive.line, directive.column, directive.offset) == (2, 1, 10)


@pytest.mark.parametrize('script', [
    '',
    '   \n\t  ',
])
def test_empty_and_whitespace_only(script):
    assert _items(script) == []


def test_comment_only_is_statement():
    items = _items('-- just a comment\n')
    assert len(items) == 1
    assert isinstance(items[0], SqlStatement)


def test_crlf_reconstruction():
    script = 'DELIMITER $$\r\nSELECT 1$$\r\nDELIMITER ;\r\nSELECT 2;\r\n'
    items = _items(script)
    assert ''.join(i.value for i in items) == script
    assert [i.new_delimiter for i in items if i.is_directive] == ['$$', ';']


def test_bytes_input():
    items = list(split_iter(b'DELIMITER $$\na$$'))
    assert items[0].is_directive
    assert items[0].new_delimiter == '$$'


@pytest.mark.parametrize('sql,expected', [
    ("'x';b;", ["'x';", 'b;']),
    ("`x`;b;", ['`x`;', 'b;']),
    ('"x";b;', ['"x";', 'b;']),
    ("'a;b';c;", ["'a;b';", 'c;']),
    ("'a''b';c;", ["'a''b';", 'c;']),
    ("a'x';b;", ["a'x';", 'b;']),
    ("SELECT 'a;b' AS x;", ["SELECT 'a;b' AS x;"]),
])
def test_string_literal_at_statement_start(sql, expected):
    assert [i.value for i in _items(sql)] == expected
    # Every chunk size must give the same boundaries.
    for size in range(1, len(sql) + 1):
        splitter = ScriptSplitter()
        got = []
        for start in range(0, len(sql), size):
            got.extend(splitter.feed(sql[start:start + size]))
        got.extend(splitter.close())
        assert [i.value for i in got] == expected


def test_strip_default_behavior():
    stripped = split('  SELECT 1;  ')
    assert stripped[0].value == 'SELECT 1;'
    raw = split('SELECT 1;  ', strip=False)
    assert raw[0].value == 'SELECT 1;  '
    # Directives are never stripped.
    directive = split('DELIMITER $$\n')[0]
    assert directive.value == 'DELIMITER $$'


def test_default_sqlparse_split_is_unchanged():
    # The default splitter does not understand DELIMITER and is unaffected.
    assert sqlparse.split('SELECT 1; SELECT 2;') == ['SELECT 1;', 'SELECT 2;']


def test_empty_initial_delimiter_rejected():
    with pytest.raises(ValueError):
        ScriptSplitter(delimiter='')


def test_reuse_after_close():
    splitter = ScriptSplitter()
    first = splitter.feed('a;') + splitter.close()
    second = splitter.feed('b;') + splitter.close()
    assert first[0].value == 'a;'
    assert second[0].value == 'b;'
    assert second[0].offset == 2


def test_top_level_split_script():
    items = sqlparse.split_script(SCRIPT)
    assert sum(i.is_directive for i in items) == 3
    assert ''.join(i.value for i in
                   sqlparse.script.split(SCRIPT, strip=False)) == SCRIPT


def test_real_mysql_dump_file(load_file):
    sql = load_file('delimiter_script.sql')
    items = _items(sql)
    directives = [i for i in items if i.is_directive]
    statements = [i for i in items if not i.is_directive]
    assert [d.new_delimiter for d in directives] == ['$$', '//', ';']
    assert len(statements) == 4
    # Each procedure/trigger keeps its internal semicolons and is
    # terminated by the switched delimiter.
    assert statements[0].value.rstrip().endswith('END$$')
    assert 'SELECT \'a;b;c\' AS literal;' in statements[0].value
    assert statements[1].value.rstrip().endswith('END $$')
    assert statements[2].value.rstrip().endswith('END//')
    # DELIMITER inside the final string literal is ordinary content.
    assert statements[3].value.strip().endswith("name = 'DELIMITER $$';")
    assert ''.join(i.value for i in items) == sql
