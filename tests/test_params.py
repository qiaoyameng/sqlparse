"""Tests for sqlparse.parameters() / sqlparse.params."""

import pytest

import sqlparse
from sqlparse import exceptions, params


def test_qmark_positions_and_spans():
    sql = "SELECT * FROM t WHERE a = ? AND b = ?"
    report = sqlparse.parameters(sql)
    assert report.style == "qmark"
    assert not report.ambiguous
    assert [p.position for p in report.parameters] == [1, 2]
    assert [p.count for p in report.parameters] == [1, 1]
    for param in report.parameters:
        (start, end), = param.spans
        assert sql[start:end] == "?"


def test_named_reused_parameter_counts_once():
    sql = "SELECT * FROM t WHERE a = :x AND b = :y AND c = :x AND d = :x"
    report = sqlparse.parameters(sql)
    assert report.style == "named"
    assert len(report.parameters) == 2
    by_name = {p.name: p for p in report.parameters}
    assert by_name["x"].count == 3
    assert by_name["y"].count == 1
    assert len(by_name["x"].spans) == 3
    for start, end in by_name["x"].spans:
        assert sql[start:end] == ":x"


def test_numeric_positions():
    sql = "SELECT * FROM t WHERE a = :1 AND b = :2 AND c = :1"
    report = sqlparse.parameters(sql)
    assert report.style == "numeric"
    assert [(p.position, p.count) for p in report.parameters] == [(1, 2), (2, 1)]


def test_dollar_numeric_positions():
    sql = "SELECT * FROM t WHERE a = $1 AND b = $2 AND c = $1"
    report = sqlparse.parameters(sql)
    assert report.style == "numeric"
    assert [(p.position, p.count) for p in report.parameters] == [(1, 2), (2, 1)]


def test_format_positions():
    sql = "SELECT * FROM t WHERE a = %s AND b = %s"
    report = sqlparse.parameters(sql)
    assert report.style == "format"
    assert [p.position for p in report.parameters] == [1, 2]


def test_pyformat_reused_parameter_counts_once():
    sql = "SELECT * FROM t WHERE a = %(x)s AND b = %(y)s AND c = %(x)s"
    report = sqlparse.parameters(sql)
    assert report.style == "pyformat"
    by_name = {p.name: p for p in report.parameters}
    assert by_name["x"].count == 2
    assert by_name["y"].count == 1


def test_postgres_cast_is_not_a_parameter():
    sql = "SELECT a::int, b :: text FROM t WHERE c = :val AND d = :val::int"
    report = sqlparse.parameters(sql)
    assert report.style == "named"
    assert len(report.parameters) == 1
    assert report.parameters[0].name == "val"
    assert report.parameters[0].count == 2


def test_percent_escape_is_not_a_parameter():
    sql = "SELECT %%s, %s"
    report = sqlparse.parameters(sql, style="format")
    assert len(report.parameters) == 1
    start, end = report.parameters[0].spans[0]
    assert sql[start:end] == "%s"
    assert start == sql.rindex("%s")
    # Odd run: one escaped pair plus a real placeholder.
    report = sqlparse.parameters("SELECT %%%s", style="format")
    assert len(report.parameters) == 1
    start, end = report.parameters[0].spans[0]
    assert "SELECT %%%s"[start:end] == "%s"
    # Escaped pyformat placeholder.
    report = sqlparse.parameters("SELECT %%(x)s, %(y)s", style="pyformat")
    assert [p.name for p in report.parameters] == ["y"]


def test_strings_comments_and_quoted_identifiers_are_ignored():
    sql = (
        "SELECT ':not', \"col:name\", `tick?`, [bracket?] "
        "FROM t -- :fake ?\n"
        "WHERE a = :real /* %s ? :nope */ AND b = '%s'"
    )
    report = sqlparse.parameters(sql)
    assert report.style == "named"
    assert [p.name for p in report.parameters] == ["real"]


def test_dollar_quoted_literal_is_ignored():
    sql = "SELECT $$ body :notparam ? $$, $tag$ %s $tag$ FROM f(:p)"
    report = sqlparse.parameters(sql)
    assert report.style == "named"
    assert [p.name for p in report.parameters] == ["p"]


def test_explicit_style_conflict_raises():
    sql = "SELECT * FROM t WHERE a = ? AND b = :name"
    with pytest.raises(exceptions.ParameterStyleConflict):
        sqlparse.parameters(sql, style="qmark")
    with pytest.raises(exceptions.ParameterStyleConflict):
        sqlparse.parameters("SELECT %s", style="named")


def test_explicit_style_match():
    sql = "SELECT * FROM t WHERE a = :x AND b = :x"
    report = sqlparse.parameters(sql, style="named")
    assert report.style == "named"
    assert report.parameters[0].count == 2


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        sqlparse.parameters("SELECT 1", style="bogus")


def test_auto_mode_keeps_ambiguity():
    sql = "SELECT * FROM t WHERE a = ? AND b = :name AND c = %s"
    report = sqlparse.parameters(sql)
    assert report.ambiguous
    assert report.style is None
    assert report.parameters == ()
    assert report.styles == ("qmark", "named", "format")


def test_no_parameters():
    report = sqlparse.parameters("SELECT 1")
    assert report.style is None
    assert report.parameters == ()
    assert not report.ambiguous
    report = sqlparse.parameters("SELECT 1", style="qmark")
    assert report.style == "qmark"
    assert report.parameters == ()


def test_same_sql_under_several_styles():
    templates = {
        "qmark": "SELECT * FROM t WHERE a = ? AND b = ?",
        "numeric": "SELECT * FROM t WHERE a = :1 AND b = :2",
        "named": "SELECT * FROM t WHERE a = :x AND b = :y",
        "format": "SELECT * FROM t WHERE a = %s AND b = %s",
        "pyformat": "SELECT * FROM t WHERE a = %(x)s AND b = %(y)s",
    }
    for style, sql in templates.items():
        report = sqlparse.parameters(sql, style=style)
        assert report.style == style
        assert len(report.parameters) == 2
        auto = sqlparse.parameters(sql)
        assert auto.style == style
        assert not auto.ambiguous
        # Every other explicit style must report a conflict.
        for other in params.PARAM_STYLES:
            if other != style:
                with pytest.raises(exceptions.ParameterStyleConflict):
                    sqlparse.parameters(sql, style=other)


def test_spans_point_into_original_sql():
    sql = "INSERT INTO t (a, b, c) VALUES (%(a)s, %(b)s, %(a)s)"
    report = sqlparse.parameters(sql)
    for param in report.parameters:
        for start, end in param.spans:
            assert sql[start:end] == f"%({param.name})s"
