from io import StringIO

import pytest

import sqlparse
from sqlparse import lexical
from sqlparse import tokens as T


def test_analyze_clean_input_is_ok():
    result = sqlparse.analyze('select * from foo;')
    assert result.ok
    assert not result.pending
    assert result.diagnostics == []
    assert result.tokens[0] == (T.Keyword.DML, 'select')
    assert ''.join(value for _, value in result.tokens) == result.text


def test_analyze_unclosed_string():
    result = sqlparse.analyze("select 'abc")
    assert not result.ok
    assert not result.pending
    assert result.tokens[-1] == (T.String.Single, "'abc")
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_STRING
    assert diag.start == 7
    assert diag.opener == "'"
    assert not diag.pending
    assert "offset 7" in diag.message
    assert result.text == "select 'abc"


def test_analyze_unclosed_quoted_names():
    for opener, ttype in [('"', T.String.Symbol), ('`', T.Name)]:
        result = sqlparse.analyze('select ' + opener + 'abc')
        (diag,) = result.diagnostics
        assert diag.kind == lexical.UNCLOSED_STRING
        assert diag.opener == opener
        assert result.tokens[-1] == (ttype, opener + 'abc')


def test_analyze_unclosed_block_comment():
    result = sqlparse.analyze('select /* abc')
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_BLOCK_COMMENT
    assert diag.start == 7
    assert diag.opener == '/*'
    assert result.tokens[-1] == (T.Comment.Multiline, '/* abc')


def test_analyze_unclosed_hint_comment():
    result = sqlparse.analyze('select /*+ hint')
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_BLOCK_COMMENT
    assert diag.opener == '/*+'
    assert result.tokens[-1] == (T.Comment.Multiline.Hint, '/*+ hint')


@pytest.mark.parametrize('opener', ['$$', '$body$'])
def test_analyze_unclosed_dollar_quote(opener):
    result = sqlparse.analyze('select ' + opener + 'abc')
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_DOLLAR_QUOTE
    assert diag.start == 7
    assert diag.opener == opener
    assert result.tokens[-1] == (T.Literal, opener + 'abc')


def test_analyze_pending_when_input_may_continue():
    result = sqlparse.analyze("select 'abc", final=False)
    assert not result.ok
    assert result.pending
    (diag,) = result.diagnostics
    assert diag.pending
    assert 'continued' in diag.message


def test_analyze_diagnostic_disappears_when_terminator_appended():
    partial = sqlparse.analyze("select 'abc", final=False)
    assert partial.pending
    complete = sqlparse.analyze(partial.text + "'")
    assert complete.ok
    assert complete.diagnostics == []
    assert complete.tokens[-1] == (T.String.Single, "'abc'")


@pytest.mark.parametrize('sql,closer', [
    ("select 'abc", "'"),
    ('select /* abc', ' */'),
    ('select $body$abc', '$body$'),
])
def test_analyze_all_constructs_recover_when_closed(sql, closer):
    assert sqlparse.analyze(sql, final=False).pending
    assert sqlparse.analyze(sql + closer).ok


def test_analyze_recovery_boundary_ignores_semicolon_in_string():
    # The semicolon inside an unterminated string must not be used to
    # resynchronize: the string swallows the rest of the input.
    result = sqlparse.analyze("select 'a; b")
    assert result.tokens[-1] == (T.String.Single, "'a; b")
    assert not any(ttype == T.Punctuation for ttype, _ in result.tokens[2:])
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_STRING


def test_analyze_recovery_boundary_ignores_semicolon_in_comment():
    result = sqlparse.analyze('select 1 /* a; b')
    assert result.tokens[-1] == (T.Comment.Multiline, '/* a; b')
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_BLOCK_COMMENT


def test_analyze_block_comments_do_not_nest():
    # The first "*/" closes the comment, no matter how many "/*" openers
    # were seen before it.
    result = sqlparse.analyze('/* a /* b */ select 1')
    assert result.ok
    assert result.tokens[0] == (T.Comment.Multiline, '/* a /* b */')
    assert (T.Keyword.DML, 'select') in result.tokens


def test_analyze_nested_opener_still_unclosed_without_closer():
    result = sqlparse.analyze('/* a /* b')
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_BLOCK_COMMENT
    assert diag.start == 0
    assert result.tokens == [(T.Comment.Multiline, '/* a /* b')]


def test_analyze_unicode_input():
    result = sqlparse.analyze("select 'héllo wörld", final=False)
    (diag,) = result.diagnostics
    assert diag.start == 7
    assert result.tokens[-1] == (T.String.Single, "'héllo wörld")
    complete = sqlparse.analyze("select 'héllo wörld'")
    assert complete.ok


def test_analyze_unicode_before_opener():
    sql = "select '日本語' as 名前 from テーブル where 名前 = '値"
    result = sqlparse.analyze(sql)
    (diag,) = result.diagnostics
    assert diag.kind == lexical.UNCLOSED_STRING
    assert result.text[diag.start] == "'"
    assert result.tokens[-1] == (T.String.Single, "'値")


def test_analyze_accepts_bytes_and_streams():
    assert sqlparse.analyze("select 'abc".encode('utf-8')).diagnostics
    assert sqlparse.analyze(StringIO("select 'abc")).diagnostics
    assert sqlparse.analyze('select 1').ok


def test_analyze_tokens_are_lossless():
    sql = "select 'a; b"
    result = sqlparse.analyze(sql)
    assert ''.join(value for _, value in result.tokens) == sql


def test_parse_behavior_unchanged_for_unclosed_input():
    # The historical parse/tokenize behavior is not affected by analyze():
    # unterminated constructs still degrade into Error tokens.
    tokens = list(sqlparse.lexer.tokenize("select 'abc"))
    assert (T.Error, "'") in tokens
    # ... and parse() still splits at the semicolon inside the
    # unterminated string, while analyze() keeps it inside the string.
    assert len(sqlparse.parse("select 'a; select 1")) == 2
    result = sqlparse.analyze("select 'a; select 1")
    assert result.tokens[-1] == (T.String.Single, "'a; select 1")
