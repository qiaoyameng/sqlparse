"""Tests for sqlparse.params placeholder style conversion."""

import sqlite3

import pytest

from sqlparse import params as paramsmod
from sqlparse.exceptions import ParameterStyleError


def test_to_qmark_named():
    sql, values = paramsmod.to_qmark(
        "SELECT * FROM t WHERE a = :a AND b = :b", {"a": 1, "b": 2}
    )
    assert sql == "SELECT * FROM t WHERE a = ? AND b = ?"
    assert values == (1, 2)


def test_to_qmark_repeated_reference():
    sql, values = paramsmod.to_qmark(
        "SELECT * FROM t WHERE a = :x OR b = :x", {"x": 7}
    )
    assert sql == "SELECT * FROM t WHERE a = ? OR b = ?"
    assert values == (7, 7)


def test_to_qmark_normalizes_dollar_and_pyformat():
    sql, values = paramsmod.to_qmark("SELECT $a, %(b)s", {"a": 1, "b": 2})
    assert sql == "SELECT ?, ?"
    assert values == (1, 2)


def test_to_qmark_positional_passthrough():
    sql, values = paramsmod.to_qmark(
        "SELECT * FROM t WHERE a = ? AND b = %s", [1, 2]
    )
    assert sql == "SELECT * FROM t WHERE a = ? AND b = ?"
    assert values == (1, 2)


def test_to_qmark_missing_value():
    with pytest.raises(ParameterStyleError, match="Missing"):
        paramsmod.to_qmark("SELECT :a, :b", {"a": 1})


def test_to_qmark_extra_value():
    with pytest.raises(ParameterStyleError, match="Unused"):
        paramsmod.to_qmark("SELECT :a", {"a": 1, "b": 2})


def test_to_qmark_wrong_params_type():
    with pytest.raises(ParameterStyleError, match="mapping"):
        paramsmod.to_qmark("SELECT :a", [1])
    with pytest.raises(ParameterStyleError, match="sequence"):
        paramsmod.to_qmark("SELECT ?", {"a": 1})


def test_to_qmark_count_mismatch():
    with pytest.raises(ParameterStyleError, match="Expected 2"):
        paramsmod.to_qmark("SELECT ?, ?", [1])


def test_mixed_styles_rejected():
    with pytest.raises(ParameterStyleError, match="mixes"):
        paramsmod.to_qmark("SELECT :a, ?", {"a": 1})
    with pytest.raises(ParameterStyleError, match="mixes"):
        paramsmod.to_named("SELECT :a, ?", {"a": 1})


def test_no_placeholders():
    assert paramsmod.to_qmark("SELECT 1", ()) == ("SELECT 1", ())
    assert paramsmod.to_named("SELECT 1", {}) == ("SELECT 1", {})
    with pytest.raises(ParameterStyleError, match="no placeholders"):
        paramsmod.to_qmark("SELECT 1", (1,))


def test_to_named_positional():
    sql, values = paramsmod.to_named(
        "SELECT * FROM t WHERE a = ? AND b = ?", [1, 2]
    )
    assert sql == "SELECT * FROM t WHERE a = :p1 AND b = :p2"
    assert values == {"p1": 1, "p2": 2}


def test_to_named_avoids_existing_names():
    # ":p1" inside a comment is not a placeholder, but generated names
    # must still avoid it.
    sql, values = paramsmod.to_named("SELECT ? /* :p1 */, ?", [1, 2])
    assert sql == "SELECT :p2 /* :p1 */, :p3"
    assert values == {"p2": 1, "p3": 2}


def test_to_named_custom_prefix():
    sql, values = paramsmod.to_named("SELECT ?", [1], prefix="arg")
    assert sql == "SELECT :arg1"
    assert values == {"arg1": 1}


def test_to_named_invalid_prefix():
    with pytest.raises(ValueError, match="prefix"):
        paramsmod.to_named("SELECT ?", [1], prefix="1bad")


def test_to_named_normalizes_named_styles():
    sql, values = paramsmod.to_named(
        "SELECT $a, %(b)s, :c", {"a": 1, "b": 2, "c": 3}
    )
    assert sql == "SELECT :a, :b, :c"
    assert values == {"a": 1, "b": 2, "c": 3}


def test_to_named_missing_and_extra():
    with pytest.raises(ParameterStyleError, match="Missing"):
        paramsmod.to_named("SELECT :a, :b", {"a": 1})
    with pytest.raises(ParameterStyleError, match="Unused"):
        paramsmod.to_named("SELECT :a", {"a": 1, "z": 2})


def test_untouched_contexts():
    # Placeholder lookalikes in strings, quoted identifiers, comments
    # and casts are not converted; surrounding text is preserved.
    source = (
        "SELECT ':a', \"x:y\", col::int, [weird:name] -- :c\n"
        "/* :block ? */ FROM t WHERE a = :real"
    )
    sql, values = paramsmod.to_qmark(source, {"real": 1})
    assert sql == (
        "SELECT ':a', \"x:y\", col::int, [weird:name] -- :c\n"
        "/* :block ? */ FROM t WHERE a = ?"
    )
    assert values == (1,)


def test_preserves_formatting():
    source = "SELECT  a,\n    :b  -- keep me\nFROM   t"
    sql, values = paramsmod.to_qmark(source, {"b": 2})
    assert sql == "SELECT  a,\n    ?  -- keep me\nFROM   t"
    assert values == (2,)


def _sqlite_roundtrip(named_sql, named_params, expected_rows):
    """Execute named SQL and its qmark conversion, compare results."""
    qmark_sql, qmark_params = paramsmod.to_qmark(named_sql, named_params)
    for value in named_params.values():
        if isinstance(value, str):
            assert value not in qmark_sql
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (a, b)")
        conn.execute("INSERT INTO t VALUES (:a, :b)", {"a": 1, "b": "x"})
        named_rows = conn.execute(named_sql, named_params).fetchall()
        qmark_rows = conn.execute(qmark_sql, qmark_params).fetchall()
        assert named_rows == qmark_rows == expected_rows
        # Parameters must not leak into the statement structure.
        conn.execute("SELECT 1 FROM t")  # table still exists
    finally:
        conn.close()
    return qmark_sql


def test_sqlite_equivalence_with_hostile_values():
    nasty = "x'); DROP TABLE t; --"
    qmark_sql = _sqlite_roundtrip(
        "SELECT * FROM t WHERE b = :val OR a = :num",
        {"val": nasty, "num": 1},
        [(1, "x")],
    )
    assert qmark_sql == "SELECT * FROM t WHERE b = ? OR a = ?"


def test_sqlite_equivalence_insert_and_repeated():
    qmark_sql, qmark_params = paramsmod.to_qmark(
        "INSERT INTO t VALUES (:v, :v)", {"v": "it's a \"quote\""}
    )
    assert qmark_sql == "INSERT INTO t VALUES (?, ?)"
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (a, b)")
        conn.execute(
            "INSERT INTO t VALUES (:v, :v)", {"v": "it's a \"quote\""}
        )
        conn.execute(qmark_sql, qmark_params)
        rows = conn.execute("SELECT a, b FROM t").fetchall()
        assert rows == [("it's a \"quote\"", "it's a \"quote\"")] * 2
    finally:
        conn.close()


def test_sqlite_to_named_roundtrip():
    sql, values = paramsmod.to_named(
        "SELECT * FROM t WHERE a = ? AND b = ?", [1, "x"]
    )
    assert sql == "SELECT * FROM t WHERE a = :p1 AND b = :p2"
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (a, b)")
        conn.execute("INSERT INTO t VALUES (?, ?)", (1, "x"))
        named_rows = conn.execute(sql, values).fetchall()
        qmark_rows = conn.execute(
            "SELECT * FROM t WHERE a = ? AND b = ?", (1, "x")
        ).fetchall()
        assert named_rows == qmark_rows == [(1, "x")]
    finally:
        conn.close()
