"""Tests for bind parameter analysis (sqlparse.params)."""

import pytest

import sqlparse
from sqlparse import params as params_mod
from sqlparse.params import (
    ParameterList,
    ParameterStyleConflict,
    ParamStyle,
    extract_parameters,
)


def occurrences_sql(parameter, sql):
    return [sql[o.start:o.end] for o in parameter.occurrences]


def test_qmark_basic():
    result = extract_parameters("SELECT * FROM t WHERE a = ? AND b = ?")
    assert result.style is ParamStyle.QMARK
    assert not result.ambiguous
    assert result.detected_styles == ("qmark",)
    assert len(result.parameters) == 2
    assert [p.position for p in result.parameters] == [1, 2]
    assert [p.count for p in result.parameters] == [1, 1]
    assert all(p.name is None for p in result.parameters)
    occ = result.parameters[0].occurrences[0]
    assert occ.start == 26 and occ.end == 27
    assert (occ.line, occ.column) == (1, 27)
    assert occ.number is None


def test_format_basic():
    result = extract_parameters("SELECT %s, %s", style="format")
    assert result.style is ParamStyle.FORMAT
    assert len(result) == 2
    assert result[0].occurrences[0].start == 7


def test_numeric_basic_and_reuse():
    result = extract_parameters("SELECT :1, :2, :1, :2, :1", style="numeric")
    assert result.style is ParamStyle.NUMERIC
    assert [(p.position, p.count, p.occurrences[0].number) for p in result] == [
        (1, 3, 1),
        (2, 2, 2),
    ]
    assert [o.number for o in result[0].occurrences] == [1, 1, 1]


def test_named_reused_three_times_is_one_parameter():
    sql = "SELECT :name FROM t WHERE :name > 1 AND :name < 9"
    result = extract_parameters(sql)
    assert result.style is ParamStyle.NAMED
    assert len(result.parameters) == 1
    parameter = result.parameters[0]
    assert parameter.name == "name"
    assert parameter.position == 1
    assert parameter.count == 3
    assert occurrences_sql(parameter, sql) == [":name", ":name", ":name"]
    assert result.names == ("name",)


def test_pyformat_reused_three_times_is_one_parameter():
    sql = "SELECT %(name)s, %(age)s, %(name)s, %(name)s"
    result = extract_parameters(sql, ParamStyle.PYFORMAT)
    assert len(result.parameters) == 2
    by_name = {p.name: p for p in result.parameters}
    assert by_name["name"].count == 3
    assert by_name["age"].count == 1
    assert [p.position for p in result.parameters] == [1, 2]
    assert result.names == ("name", "age")
    assert occurrences_sql(by_name["name"], sql) == [
        "%(name)s",
        "%(name)s",
        "%(name)s",
    ]


def test_named_distinct_parameters_keep_order():
    sql = "SELECT :a, :b, :a, :c"
    result = extract_parameters(sql, "named")
    assert [(p.name, p.count) for p in result] == [
        ("a", 2),
        ("b", 1),
        ("c", 1),
    ]


def test_positions_multiline():
    sql = "SELECT :a\nFROM t\nWHERE :a"
    result = extract_parameters(sql, "named")
    assert [(o.line, o.column) for o in result[0].occurrences] == [
        (1, 8),
        (3, 7),
    ]
    assert [sql[o.start:o.end] for o in result[0].occurrences] == [":a", ":a"]


def test_postgres_cast_double_colon():
    named = extract_parameters("SELECT x::int, :name::text FROM t", "named")
    assert [p.name for p in named.parameters] == ["name"]
    numeric = extract_parameters("SELECT x::int, :1::text FROM t", "numeric")
    assert [p.occurrences[0].number for p in numeric.parameters] == [1]
    qmark = extract_parameters("SELECT x::int, ?::jsonb FROM t", "qmark")
    assert len(qmark.parameters) == 1
    assert qmark[0].occurrences[0].value == "?"
    pyformat = extract_parameters("SELECT x::int, %(n)s::text", "pyformat")
    assert pyformat.names == ("n",)


def test_param_right_after_double_colon_cast():
    sql = "SELECT (now()::date):1 FROM t"
    result = extract_parameters(sql, "numeric")
    assert len(result.parameters) == 1
    assert result[0].occurrences[0].number == 1


def test_percent_escape_and_modulo_ignored():
    sql = "SELECT 100 %% done, a % b, %s, %%(x)s"
    result = extract_parameters(sql, "format")
    assert len(result.parameters) == 1
    assert result[0].occurrences[0].value == "%s"
    # pyformat only sees %(...)s markers; %% must not look like an escape
    pyresult = extract_parameters(
        "SELECT %(x)s, 100 %% done", style=ParamStyle.PYFORMAT
    )
    assert [p.name for p in pyresult.parameters] == ["x"]


def test_quoted_identifiers_and_strings_ignored():
    sql = (
        'SELECT \'? :n %s %(a)s\', "col?", "weird ::x", '
        "`b?`, [bracket?], '100%% done' FROM t WHERE x = ?"
    )
    prefix = (
        'SELECT \'? :n %s %(a)s\', "col?", "weird ::x", '
        "`b?`, [bracket?], '100%% done' FROM t WHERE x = "
    )
    assert len(extract_parameters(prefix + "?", "qmark").parameters) == 1
    assert extract_parameters(prefix + ":y", "named").names == ("y",)
    assert len(extract_parameters(prefix + "%s", "format").parameters) == 1
    assert extract_parameters(prefix + "%(y)s", "pyformat").names == ("y",)
    assert extract_parameters(prefix + "1", "qmark").parameters == ()


def test_dollar_quoted_string_ignored():
    sql = "SELECT $$body ? :n %(a)s$$, ? FROM t"
    result = extract_parameters(sql, "qmark")
    assert len(result.parameters) == 1
    assert result[0].occurrences[0].start == sql.rindex("?")


def test_comments_ignored():
    sql = (
        "SELECT :real -- :notparam ?\n"
        ", /* :also %s */ :more\n"
        ", # :hashcomment\n"
        ":final"
    )
    result = extract_parameters(sql, "named")
    assert result.names == ("real", "more", "final")


def test_explicit_style_conflict_qmark_vs_named():
    with pytest.raises(ParameterStyleConflict) as exc:
        extract_parameters("SELECT ?, :a", style="qmark")
    assert exc.value.style is ParamStyle.QMARK
    assert exc.value.conflicting_kinds == ("named",)


@pytest.mark.parametrize(
    "style,sql",
    [
        ("qmark", "SELECT :a"),
        ("numeric", "SELECT ?"),
        ("named", "SELECT %s"),
        ("format", "SELECT %(a)s"),
        ("pyformat", "SELECT %s"),
        ("format", "SELECT :1"),
    ],
)
def test_explicit_style_conflicts(style, sql):
    with pytest.raises(ParameterStyleConflict):
        extract_parameters(sql, style=style)


def test_postgres_native_dollar_conflicts_explicitly():
    with pytest.raises(ParameterStyleConflict) as exc:
        extract_parameters("SELECT $1, ?", style="qmark")
    assert exc.value.conflicting_kinds == ("dollar",)


def test_auto_detect_each_style():
    sqls = {
        ParamStyle.QMARK: "SELECT ?",
        ParamStyle.NUMERIC: "SELECT :1",
        ParamStyle.NAMED: "SELECT :name",
        ParamStyle.FORMAT: "SELECT %s",
        ParamStyle.PYFORMAT: "SELECT %(name)s",
    }
    for expected_style, sql in sqls.items():
        result = extract_parameters(sql)
        assert result.style is expected_style
        assert not result.ambiguous


def test_auto_mode_ambiguous_mixed_styles():
    result = extract_parameters("SELECT ?, :a, %s")
    assert result.ambiguous
    assert result.style is None
    assert result.parameters == ()
    assert result.detected_styles == ("qmark", "named", "format")
    assert set(result.candidates) == {"qmark", "named", "format"}
    assert len(result.candidates["qmark"]) == 1
    assert len(result.candidates["named"]) == 1
    assert len(result.candidates["format"]) == 1


def test_auto_mode_ambiguous_preserves_reuse_in_candidates():
    result = extract_parameters(
        "SELECT %(name)s, %(name)s, :other"
    )
    assert result.ambiguous
    pyformat_params = result.candidates["pyformat"]
    assert len(pyformat_params) == 1
    assert pyformat_params[0].name == "name"
    assert pyformat_params[0].count == 2
    assert result.candidates["named"][0].name == "other"


def test_auto_mode_dollar_marker_is_ambiguous():
    result = extract_parameters("SELECT $1, $2")
    assert result.ambiguous
    assert result.style is None
    assert result.detected_styles == ("dollar",)
    assert result.candidates == {}
    assert result.parameters == ()


def test_no_markers():
    result = extract_parameters("SELECT 1 FROM t WHERE x::int = 1")
    assert not result.ambiguous
    assert result.style is None
    assert len(result) == 0
    assert result.names == ()


def test_multi_statement_offsets_are_absolute():
    sql = "SELECT ?; SELECT ?"
    result = extract_parameters(sql, "qmark")
    positions = [p.occurrences[0].start for p in result.parameters]
    assert positions == [7, 17]
    assert [sql[s:e] for s, e in ((p.occurrences[0].start, p.occurrences[0].end)
                                 for p in result)] == ["?", "?"]


def test_result_is_iterable_and_indexable():
    result = extract_parameters("SELECT :a, :b")
    assert result[0].name == "a"
    assert [p.name for p in result] == ["a", "b"]
    assert len(result) == 2


def test_invalid_style_raises_value_error():
    with pytest.raises(ValueError):
        extract_parameters("SELECT ?", style="bogus")


def test_does_not_modify_sql():
    sql = "SELECT :name FROM t WHERE :name = %(x)s"
    original = sql
    extract_parameters(sql)
    assert sql == original


def test_scan_uses_placeholder_tokens_only():
    # regression guard: a standalone colon and := assignment are ignored
    result = extract_parameters("x := :a || ':'", style="named")
    assert result.names == ("a",)
    qresult = extract_parameters("SELECT jsonb_col ? 'key' FROM t")
    assert qresult.style is ParamStyle.QMARK


def test_public_api_exports():
    assert sqlparse.extract_parameters is extract_parameters
    assert sqlparse.ParamStyle is ParamStyle
    assert sqlparse.ParameterStyleConflict is ParameterStyleConflict
    assert isinstance(extract_parameters("SELECT ?"), ParameterList)
    assert params_mod.DOLLAR == "dollar"
