#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

import pytest

import sqlparse
from sqlparse import analysis, tokens as T


def test_complete_input_is_ok():
    result = sqlparse.analyze("SELECT * FROM t WHERE a = 'x';")
    assert result.ok
    assert result.status == 'ok'
    assert result.diagnostics == []
    assert result.text == "SELECT * FROM t WHERE a = 'x';"


def test_unclosed_string_is_pending_and_recovers_when_completed():
    partial = "SELECT * FROM t WHERE name = 'ada"
    result = sqlparse.analyze(partial)
    assert result.status == 'pending'
    assert not result.ok
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == analysis.UNCLOSED_STRING
    assert diag.start == partial.index("'")
    assert diag.end == len(partial)
    assert diag.opener == "'"
    # the unterminated string swallows the rest of the input
    assert result.tokens[-1] == (T.String.Single, "'ada")

    completed = sqlparse.analyze(partial + "'")
    assert completed.status == 'ok'
    assert completed.diagnostics == []
    assert completed.tokens[-1] == (T.String.Single, "'ada'")


def test_final_input_reports_error():
    result = sqlparse.analyze("SELECT 'oops", final=True)
    assert result.status == 'error'
    assert len(result.diagnostics) == 1


def test_unclosed_block_comment():
    result = sqlparse.analyze("SELECT 1 /* note")
    assert result.status == 'pending'
    diag = result.diagnostics[0]
    assert diag.kind == analysis.UNCLOSED_BLOCK_COMMENT
    assert diag.start == 9
    assert diag.opener == '/*'
    assert result.tokens[-1] == (T.Comment.Multiline, '/* note')

    completed = sqlparse.analyze("SELECT 1 /* note */")
    assert completed.status == 'ok'


def test_unclosed_hint_comment():
    result = sqlparse.analyze("SELECT /*+ INDEX(t) */ 1 /*+ ")
    assert result.status == 'pending'
    diag = result.diagnostics[0]
    assert diag.kind == analysis.UNCLOSED_BLOCK_COMMENT
    assert diag.opener == '/*+'
    assert result.tokens[-1][0] == T.Comment.Multiline.Hint


def test_unclosed_dollar_quote():
    result = sqlparse.analyze("SELECT $tag$body")
    assert result.status == 'pending'
    diag = result.diagnostics[0]
    assert diag.kind == analysis.UNCLOSED_DOLLAR_QUOTE
    assert diag.start == 7
    assert diag.opener == '$tag$'

    completed = sqlparse.analyze("SELECT $tag$body$tag$")
    assert completed.status == 'ok'


def test_unclosed_quoted_name():
    result = sqlparse.analyze('SELECT "col')
    assert result.status == 'pending'
    diag = result.diagnostics[0]
    assert diag.kind == analysis.UNCLOSED_QUOTED_NAME
    assert result.tokens[-1] == (T.String.Symbol, '"col')

    result = sqlparse.analyze('SELECT `col')
    assert result.diagnostics[0].kind == analysis.UNCLOSED_QUOTED_NAME
    assert result.tokens[-1] == (T.Name, '`col')


def test_no_forced_recovery_at_semicolon_inside_string():
    # A semicolon inside an unterminated string is not a statement
    # boundary and must not be used as a recovery point.
    result = sqlparse.analyze("SELECT ';")
    assert result.status == 'pending'
    assert len(result.diagnostics) == 1
    assert result.tokens[-1] == (T.String.Single, "';")
    assert (T.Punctuation, ';') not in result.tokens


def test_nested_comments_do_not_nest():
    # sqlparse pairs "/*" with the first following "*/"; comments do
    # not nest.  A comment closed that way is complete.
    result = sqlparse.analyze("/* outer /* inner */")
    assert result.status == 'ok'
    assert result.tokens == [(T.Comment.Multiline, '/* outer /* inner */')]

    # Without any "*/" the outermost opener is reported, once.
    result = sqlparse.analyze("/* outer /* inner")
    assert result.status == 'pending'
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].start == 0

    # Text after the first "*/" is lexed normally, not as comment.
    result = sqlparse.analyze("/* a /* b */ SELECT 1")
    assert result.status == 'ok'
    assert result.tokens[0] == (T.Comment.Multiline, '/* a /* b */')
    assert (T.Keyword.DML, 'SELECT') in result.tokens


def test_unicode_input():
    partial = "SELECT '日本語"
    result = sqlparse.analyze(partial)
    assert result.status == 'pending'
    diag = result.diagnostics[0]
    assert diag.start == partial.index("'")
    assert result.text == partial
    assert result.tokens[-1] == (T.String.Single, "'日本語")

    completed = sqlparse.analyze("SELECT '日本語' -- コメント")
    assert completed.status == 'ok'


def test_bytes_and_file_like_input():
    import io

    assert sqlparse.analyze(b"SELECT 'x").status == 'pending'
    result = sqlparse.analyze("SELECT 'é".encode('latin-1'),
                              encoding='latin-1')
    assert result.status == 'pending'
    assert sqlparse.analyze(io.StringIO("SELECT 1")).status == 'ok'


def test_invalid_input_type():
    with pytest.raises(TypeError):
        sqlparse.analyze(42)


def test_parse_and_tokenize_results_unchanged():
    # The fault-tolerant entry point must not change the behavior of
    # the existing API, even for broken input.
    samples = [
        "SELECT * FROM t WHERE name = 'ada",
        "SELECT 1 /* note",
        "SELECT $tag$body",
        "SELECT ';'; SELECT 1",
        "/* outer /* inner */ SELECT 1",
    ]
    for sample in samples:
        before = list(sqlparse.lexer.tokenize(sample))
        sqlparse.analyze(sample)
        sqlparse.analyze(sample, final=True)
        assert list(sqlparse.lexer.tokenize(sample)) == before
        # parse() keeps working and does not raise
        sqlparse.parse(sample)


def test_tokens_match_lexer_for_complete_input():
    sql = ("SELECT a, 'b''c', \"d\" FROM t -- note\n"
           "WHERE x = $$y$$ AND z = $t$w$t$ /* c */;")
    result = sqlparse.analyze(sql)
    assert result.status == 'ok'
    assert result.tokens == list(sqlparse.lexer.tokenize(sql))
