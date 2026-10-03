#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Tests for explicit script splitting with DELIMITER directives."""

import random

import pytest

import sqlparse
from sqlparse import (
    ScriptSegment,
    ScriptSplitter,
    split_script,
    split_script_statements,
)
from sqlparse.exceptions import DelimiterError

# A realistic mysqldump-style script: two procedures, comments and a
# plain query, with two delimiter switches and multi-character tokens.
SCRIPT = """\
--
-- export of example procedures
--
USE `exampledb`;

# switch the statement terminator for routine bodies
DELIMITER $$

CREATE PROCEDURE `add_user`(IN name VARCHAR(100))
BEGIN
  INSERT INTO users (name) VALUES (name);
  SELECT LAST_INSERT_ID() AS id;
END$$

CREATE PROCEDURE `count_users`()
BEGIN
  /* semicolons; inside; the; body stay; untouched */
  SELECT 'a; test' AS label, "quote;x" AS other;
  SELECT COUNT(*) AS total FROM users;
END $$

DELIMITER ;

-- back to normal statements
SELECT 1 AS plain_query;
SELECT 2 AS another;
"""

DIRECTIVE_KW = "DELIMITER"


def _segments(script, **kwargs):
    return split_script(script, **kwargs)


def _chunked(script, sizes, **kwargs):
    splitter = ScriptSplitter(**kwargs)
    segments = []
    index = 0
    size_cycle = list(sizes)
    k = 0
    while index < len(script):
        size = size_cycle[k % len(size_cycle)]
        k += 1
        segments.extend(splitter.feed(script[index:index + size]))
        index += size
    segments.extend(splitter.close())
    return segments


def _tuples(segments):
    return [
        (s.kind, s.value, s.delimiter, s.start, s.end) for s in segments
    ]


def test_split_script_roundtrip():
    segments = _segments(SCRIPT)
    assert "".join(s.value for s in segments) == SCRIPT


def test_directives_recognized():
    segments = _segments(SCRIPT)
    directives = [s for s in segments if s.is_directive]
    assert [s.delimiter for s in directives] == ["$$", ";"]
    assert all(s.kind == "delimiter" for s in directives)
    assert all(not s.is_directive for s in segments if s not in directives)


def test_procedure_bodies_keep_inner_semicolons():
    statements = split_script_statements(SCRIPT)
    proc1 = next(s for s in statements if s.startswith("CREATE PROCEDURE `add_user`"))
    assert proc1.endswith("END$$")
    assert "VALUES (name);" in proc1
    assert "AS id;" in proc1

    proc2 = next(s for s in statements if s.startswith("CREATE PROCEDURE `count_users`"))
    assert proc2.endswith("END $$")
    assert proc2.count(";") >= 4

    assert statements[-2].endswith("SELECT 1 AS plain_query;")
    assert statements[-1] == "SELECT 2 AS another;"
    assert statements[-2].startswith("-- back to normal statements")


def test_segment_offsets():
    for segment in _segments(SCRIPT):
        assert SCRIPT[segment.start:segment.end] == segment.value


def test_char_by_char_equals_single_input():
    expected = _tuples(_segments(SCRIPT))
    got = _tuples(_chunked(SCRIPT, [1]))
    assert got == expected


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7, 13, 64, 1000])
def test_chunk_sizes_match(size):
    expected = _tuples(_segments(SCRIPT))
    assert _tuples(_chunked(SCRIPT, [size])) == expected


def test_random_chunking_matches():
    expected = _tuples(_segments(SCRIPT))
    random.seed(20261003)
    for _ in range(300):
        sizes = [random.randint(1, 12) for _ in range(16)]
        assert _tuples(_chunked(SCRIPT, sizes)) == expected


def test_directive_word_inside_literals_and_comments_is_content():
    sql = (
        "SELECT 'DELIMITER $$' AS a;\n"
        'SELECT "DELIMITER ;" AS b;\n'
        "SELECT `DELIMITER xx` AS c;\n"
        "SELECT /* DELIMITER zz */ 1 AS d;\n"
        "SELECT -- DELIMITER qq\n 2 AS e;\n"
        "SELECT # DELIMITER qq\n 3 AS f;\n"
    )
    segments = _segments(sql)
    assert not [s for s in segments if s.is_directive]
    terminated = [s for s in segments if s.value.endswith(";")]
    assert len(terminated) == 6
    # chunking gives the exact same boundaries
    assert _tuples(_chunked(sql, [1])) == _tuples(segments)


def test_directive_only_between_statements():
    # DELIMITER glued to an identifier or appearing mid-statement is SQL.
    assert not [s for s in _segments("DELIMITERX 1;") if s.is_directive]
    assert not [s for s in _segments("DELIMITER; SELECT 1;") if s.is_directive]
    mid = "SELECT 1 DELIMITER $$ ; SELECT 2;"
    assert not [s for s in _segments(mid) if s.is_directive]
    # the keyword name DELIMITERS must not trigger either
    assert not [s for s in _segments("SELECT DELIMITERS;") if s.is_directive]


def test_multiple_switches_and_custom_delimiters():
    sql = (
        "A;\n"
        "DELIMITER GO\n"
        "B(); GO\n"
        "DELIMITER @@\n"
        "C@@\n"
        "DELIMITER ;\n"
        "D;"
    )
    segments = _segments(sql)
    assert [s.delimiter for s in segments if s.is_directive] == [
        "GO",
        "@@",
        ";",
    ]
    assert split_script_statements(sql) == ["A;", "B(); GO", "C@@", "D;"]


def test_word_delimiter_needs_word_boundaries():
    sql = (
        "DELIMITER go\n"
        "INSERT INTO t VALUES ('gogo');\n"
        "SELECT xgo FROM t;\n"
        "go\n"
    )
    statements = split_script_statements(sql)
    assert statements == [
        "INSERT INTO t VALUES ('gogo');\nSELECT xgo FROM t;\ngo"
    ]


def test_punctuation_delimiters_take_precedence_over_comment_markers():
    # "//" must terminate a statement and must not start a "/*" comment.
    sql = (
        "DELIMITER //\n"
        "SELECT 'semi;inside' AS q//\n"
        "DELIMITER ;\n"
        "SELECT 3;"
    )
    segments = _segments(sql)
    assert [s.delimiter for s in segments if s.is_directive] == ["//", ";"]
    assert any(
        s.value == "SELECT 'semi;inside' AS q//" for s in segments
    )
    assert _tuples(_chunked(sql, [1])) == _tuples(segments)


@pytest.mark.parametrize(
    "initial,sql",
    [
        ("/", "a/b/\nc/\n"),
        ("-", "x-y-\nz-\n"),
        ("//", "x//\ny//\n"),
    ],
)
def test_single_char_punctuation_delimiters(initial, sql):
    segments = _segments(sql, delimiter=initial)
    assert _tuples(_chunked(sql, [1], delimiter=initial)) == _tuples(
        segments
    )
    assert "".join(s.value for s in segments) == sql


def test_directive_case_insensitive_and_tabs():
    sql = "delimiter\t$$\nx$$\nDeLiMitEr ;\ny;"
    segments = _segments(sql)
    assert [s.delimiter for s in segments if s.is_directive] == ["$$", ";"]


def test_crlf_and_cr_line_endings():
    sql = "SELECT 1;\r\nDELIMITER $$\r\nSELECT 2$$\r"
    segments = _segments(sql)
    assert [s.delimiter for s in segments if s.is_directive] == ["$$"]
    assert "".join(s.value for s in segments) == sql
    assert _tuples(_chunked(sql, [1])) == _tuples(segments)


def test_leading_trivia_before_directive_is_own_segment():
    sql = "  \nDELIMITER $$\nX$$"
    segments = _segments(sql)
    assert segments[0].value == "  \n"
    assert segments[1].is_directive


def test_trailing_sql_without_terminator():
    segments = _segments("SELECT 1;\nSELECT 2")
    assert [s.value for s in segments] == ["SELECT 1;", "\nSELECT 2"]


def test_unterminated_string_is_trailing_sql():
    sql = "SELECT 'unclosed ; ; ;"
    segments = _segments(sql)
    assert segments[-1].value == sql
    assert _tuples(_chunked(sql, [1])) == _tuples(segments)


def test_default_split_is_unchanged():
    # The new feature must not alter sqlparse.split() semantics.
    sql = "SELECT 1; SELECT 'x;y';"
    assert sqlparse.split(sql) == ["SELECT 1;", "SELECT 'x;y';"]
    # DELIMITER is an unknown token for the classic splitter and does
    # not change the terminator.
    assert sqlparse.split("DELIMITER $$\nSELECT 1$$") == [
        "DELIMITER $$\nSELECT 1$$"
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1;\nDELIMITER\nSELECT 2",
        "SELECT 1;\nDELIMITER\r\n",
        "DELIMITER   \n",
        "DELIMITER",
        "DELIMITER ",
        "SELECT 1;\nDELIMITER $$ xx\n",
        "DELIMITER $$ yy zz\n",
    ],
)
def test_invalid_directives_raise_located_error(sql):
    with pytest.raises(DelimiterError) as info:
        split_script(sql)
    error = info.value
    assert error.line is not None
    assert error.column is not None
    assert error.offset is not None


def test_error_position_is_directive_start():
    try:
        split_script("SELECT 1;\n\nDELIMITER\n")
    except DelimiterError as error:
        assert (error.line, error.column) == (3, 1)
        assert error.offset == len("SELECT 1;\n\n")
    else:  # pragma: no cover
        raise AssertionError("expected DelimiterError")


def test_error_position_points_at_extra_token():
    try:
        split_script("   DELIMITER $$ xx\n")
    except DelimiterError as error:
        assert (error.line, error.column) == (1, 17)
        assert error.offset == 16
    else:  # pragma: no cover
        raise AssertionError("expected DelimiterError")


def test_error_is_sqlparse_error_subclass():
    assert issubclass(DelimiterError, sqlparse.SQLParseError)


def test_streaming_error_reported_on_incremental_feed():
    splitter = ScriptSplitter()
    with pytest.raises(DelimiterError) as info:
        splitter.feed("SELECT 1;\nDELIM")
        splitter.feed("ITER\n")
    assert info.value.line == 2


def test_close_twice_is_idempotent():
    splitter = ScriptSplitter()
    assert splitter.close() == []
    assert splitter.close() == []


def test_feed_after_close_raises():
    splitter = ScriptSplitter()
    splitter.close()
    with pytest.raises(RuntimeError):
        splitter.feed("SELECT 1;")


def test_invalid_initial_delimiter():
    with pytest.raises(ValueError):
        ScriptSplitter(delimiter="")


def test_bytes_input():
    sql = b"SELECT 1;\nDELIMITER $$\nSELECT 2$$"
    segments = split_script(sql)
    assert [s.delimiter for s in segments if s.is_directive] == ["$$"]


def test_file_like_input():
    import io

    segments = split_script(io.StringIO("SELECT 1;\nDELIMITER $$\nx$$"))
    assert any(s.is_directive for s in segments)


def test_segment_is_namedtuple_like():
    segment = ScriptSegment("sql", "SELECT 1;", 0, 10, None)
    assert segment.kind == "sql"
    assert not segment.is_directive
    assert segment._fields == ("kind", "value", "start", "end", "delimiter")


def test_strip_semicolon_helper():
    sql = "A;\nDELIMITER $$\nB$$\nDELIMITER ;\nC;"
    assert split_script_statements(sql, strip_semicolon=True) == [
        "A",
        "B$$",
        "C",
    ]
