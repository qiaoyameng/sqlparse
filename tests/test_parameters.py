import sqlite3

import pytest

import sqlparse
from sqlparse.parameters import convert_params


def test_named_to_qmark_repeats_values_and_preserves_text():
    source = (
        "SELECT '?:x', \"quoted :name\", `:name`,\n"
        "-- :name\n"
        "/* ? */ x::TEXT, $body$:name$body$\n"
        "FROM t WHERE a = :name AND b = :other AND c = :name;"
    )

    converted, params = convert_params(
        source, {"name": "O'Neil", "other": "x = 'y'; DROP TABLE t;--"},
    )

    assert converted == (
        "SELECT '?:x', \"quoted :name\", `:name`,\n"
        "-- :name\n"
        "/* ? */ x::TEXT, $body$:name$body$\n"
        "FROM t WHERE a = ? AND b = ? AND c = ?;"
    )
    assert params == ("O'Neil", "x = 'y'; DROP TABLE t;--", "O'Neil")


@pytest.mark.parametrize(
    ("source", "style", "params", "expected_sql", "expected_params"),
    [
        (
            "SELECT ?",
            "named",
            ("a",),
            "SELECT :param_1",
            {"param_1": "a"},
        ),
        (
            "SELECT %s, %s",
            "named",
            ("a", "b"),
            "SELECT :param_1, :param_2",
            {"param_1": "a", "param_2": "b"},
        ),
        (
            "SELECT :a, :b, :a",
            "numeric",
            {"a": 1, "b": 2},
            "SELECT :1, :2, :1",
            (1, 2),
        ),
        (
            "SELECT $1, $2, $1",
            "qmark",
            ("a", "b"),
            "SELECT ?, ?, ?",
            ("a", "b", "a"),
        ),
        (
            "SELECT ?, ?",
            "numeric",
            ("a", "b"),
            "SELECT :1, :2",
            ("a", "b"),
        ),
        (
            "SELECT ?, ?",
            "named",
            ("a", "b"),
            "SELECT :param_1, :param_2",
            {"param_1": "a", "param_2": "b"},
        ),
        (
            "SELECT %(a)s",
            "named",
            {"a": "x"},
            "SELECT :a",
            {"a": "x"},
        ),
        (
            "SELECT :a",
            "pyformat",
            {"a": "x"},
            "SELECT %(a)s",
            {"a": "x"},
        ),
    ],
)
def test_parameter_style_conversion(
    source, style, params, expected_sql, expected_params
):
    converted, converted_params = convert_params(source, params, style)
    assert converted == expected_sql
    assert converted_params == expected_params


def test_generated_name_avoids_existing_identifiers():
    source = "SELECT param_1 AS param_2 WHERE x = ?"

    converted, params = convert_params(source, ("value",), "named")

    assert converted == "SELECT param_1 AS param_2 WHERE x = :param_3"
    assert params == {"param_3": "value"}


def test_existing_named_placeholder_is_renamed_on_identifier_collision():
    source = "SELECT param_1 FROM t WHERE x = :param_1"

    converted, params = convert_params(source, {"param_1": "value"}, "named")

    assert converted == "SELECT param_1 FROM t WHERE x = :param_2"
    assert params == {"param_2": "value"}


def test_type_cast_and_quotes_are_not_placeholders():
    source = "SELECT CAST(? AS TEXT), ?::TEXT, '?', \"?\", [?] WHERE x = ?"

    converted, params = convert_params(
        source, ("first", "second", "third"), "named"
    )

    assert converted == (
        "SELECT CAST(:param_1 AS TEXT), :param_2::TEXT, '?', \"?\", [?] "
        "WHERE x = :param_3"
    )
    assert params == {
        "param_1": "first",
        "param_2": "second",
        "param_3": "third",
    }


def test_postgres_json_question_mark_operators_are_left_unchanged():
    source = "SELECT data ?| 'a', data ?& 'b'"

    converted, params = convert_params(source, None, "named")

    assert converted == source
    assert params == {}


def test_no_placeholders_requires_no_values():
    converted, params = convert_params("SELECT 1", None, "qmark")
    assert converted == "SELECT 1"
    assert params == ()


@pytest.mark.parametrize(
    ("source", "params", "target", "message"),
    [
        ("SELECT :a", {}, "qmark", "Missing parameter values: a"),
        (
            "SELECT :a",
            {"a": 1, "b": 2},
            "qmark",
            "Unexpected parameter values: b",
        ),
        ("SELECT ?, ?", (1,), "qmark", "Expected 2 parameter values, got 1"),
        ("SELECT ?, ?", (1, 2, 3), "qmark", "Expected 2 parameter values, got 3"),
        ("SELECT ? AND :a", (1, 2), "qmark", "Cannot mix parameter styles"),
        ("SELECT %s, %(a)s", (1, 2), "qmark", "Cannot mix parameter styles"),
        ("SELECT $1", (), "qmark", "Missing parameter values: 1"),
        ("SELECT $1", (1, 2), "qmark", "Unexpected parameter values: 2"),
        (
            "SELECT $1",
            {"1": 1},
            "qmark",
            "Numeric parameters require a sequence",
        ),
    ],
)
def test_invalid_parameter_mapping(source, params, target, message):
    with pytest.raises(ValueError, match=message):
        convert_params(source, params, target)


def test_named_parameter_type_must_be_mapping():
    with pytest.raises(ValueError, match="named parameters require a mapping"):
        convert_params("SELECT :a", ["a"], "qmark")


def test_invalid_target_style():
    with pytest.raises(ValueError, match="Unsupported target parameter style"):
        convert_params("SELECT ?", [], "unknown")


def test_convert_paramstyle_alias():
    assert sqlparse.convert_paramstyle is sqlparse.convert_params


def test_sqlite_named_and_qmark_queries_are_equivalent():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE t (id INTEGER, note TEXT)")
        values = [
            (1, "O'Neil"),
            (2, "x = 'y'; DROP TABLE t;--"),
            (3, '"quoted" ? :note %(note)s'),
        ]
        connection.executemany("INSERT INTO t VALUES (?, ?)", values)

        source_sql = (
            "SELECT id FROM t WHERE note = :note AND note <> :other "
            "OR note = :note"
        )
        source_params = {
            "note": "x = 'y'; DROP TABLE t;--",
            "other": "different",
        }
        converted_sql, qmark_params = convert_params(
            source_sql, source_params, "qmark"
        )

        source_result = connection.execute(source_sql, source_params).fetchall()
        converted_result = connection.execute(
            converted_sql, qmark_params
        ).fetchall()

        assert source_result == converted_result == [(2,)]
        assert "DROP TABLE" not in converted_sql
        assert all(value not in converted_sql for value in source_params.values())
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute(converted_sql)

        roundtrip_sql, named_params = convert_params(
            converted_sql, qmark_params, "named"
        )
        roundtrip_result = connection.execute(
            roundtrip_sql, named_params
        ).fetchall()
        assert roundtrip_result == converted_result
    finally:
        connection.close()
