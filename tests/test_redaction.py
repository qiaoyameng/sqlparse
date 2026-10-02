import pytest

import sqlparse
from sqlparse import lexer, redaction, tokens as T


SENSITIVE = ("Alice", "O''Brien", "-4200", "9001", "$body$")


def test_redacts_literals_and_preserves_bind_placeholders():
    result = sqlparse.redact(
        "SELECT * FROM customers WHERE name = 'Alice' AND amount = -4200 "
        "AND active = TRUE AND id = :customer_id")

    assert result.text == (
        "SELECT * FROM customers WHERE name = [REDACTED] "
        "AND amount = [REDACTED_NUMBER] "
        "AND active = [REDACTED_BOOLEAN] AND id = :customer_id")
    assert result.shape == (
        "SELECT * FROM customers WHERE name = ? AND amount = ? "
        "AND active = ? AND id = :BIND")
    assert result.shape_id.startswith("sqlshape-")
    assert "Alice" not in result.text
    assert "4200" not in result.text


def test_literal_values_share_a_stable_shape():
    first = sqlparse.redact(
        "SELECT amount FROM customers WHERE name = 'Alice' "
        "AND amount < -4200 AND active = TRUE")
    second = sqlparse.redact(
        "select amount from customers where name = 'Bob' "
        "and amount < -9001 and active = false")

    assert first.text != second.text
    assert first.shape == second.shape
    assert first.shape_id == second.shape_id


def test_escaped_strings_dollar_quotes_and_negative_numbers():
    statement = (
        "SELECT 'O''Brien', -4200, $$Alice 9001$$, "
        "$body$name='Alice'$body$ FROM t")
    result = sqlparse.redact(statement)

    assert result.text == (
        "SELECT [REDACTED], [REDACTED_NUMBER], [REDACTED], "
        "[REDACTED] FROM t")
    assert result.shape == "SELECT ?, ?, ?, ? FROM t"
    for secret in SENSITIVE:
        assert secret not in result.text + result.shape + result.shape_id


def test_collapses_large_in_lists_but_keeps_small_lists():
    small = sqlparse.redact("SELECT * FROM t WHERE id IN (1, 2)")
    large = sqlparse.redact(
        "SELECT * FROM t WHERE id IN (1, 2, 3, 4, 5)")
    configured = sqlparse.redact(
        "SELECT * FROM t WHERE id IN (1, 2, 3)", in_list_threshold=3)

    assert small.text == (
        "SELECT * FROM t WHERE id IN "
        "([REDACTED_NUMBER], [REDACTED_NUMBER])")
    assert large.text == "SELECT * FROM t WHERE id IN ([REDACTED_VALUES])"
    assert large.shape == "SELECT * FROM t WHERE id IN (?)"
    assert configured.text == (
        "SELECT * FROM t WHERE id IN ([REDACTED_VALUES])")
    configured_marker = sqlparse.redact(
        "SELECT * FROM t WHERE id IN (1, 2, 3)", in_list_threshold=3,
        in_list_marker="?")
    assert configured_marker.text == "SELECT * FROM t WHERE id IN (?)"


def test_typed_literal_lists_collapse_and_subqueries_do_not():
    typed = sqlparse.redact(
        "SELECT * FROM t WHERE d IN ("
        "DATE '2024-01-01', DATE '2024-01-02', DATE '2024-01-03', "
        "DATE '2024-01-04', DATE '2024-01-05')")
    subquery = sqlparse.redact(
        "SELECT * FROM t WHERE id IN "
        "(SELECT id FROM s WHERE secret = 'Alice')")

    assert typed.text == (
        "SELECT * FROM t WHERE d IN ([REDACTED_VALUES])")
    assert typed.shape == "SELECT * FROM t WHERE d IN (?)"
    assert "Alice" not in subquery.text + subquery.shape
    assert "SELECT" in subquery.shape


def test_comments_are_removed_and_hints_are_configurable():
    statement = (
        "SELECT /* Alice 9001 */ amount FROM t "
        "WHERE x = 1 /*+ INDEX(t idx_secret Alice) */ -- name Alice")

    stripped = sqlparse.redact(statement)
    masked = sqlparse.redact(statement, hint_policy="mask")
    preserved = sqlparse.redact(statement, hint_policy="preserve")

    assert "Alice" not in stripped.text
    assert "9001" not in stripped.text
    assert "idx_secret" not in stripped.shape
    assert "/* [REDACTED_HINT] */" in masked.text
    assert "INDEX(t idx_secret Alice)" in preserved.text
    assert "Alice" not in preserved.shape
    assert stripped.shape == masked.shape == preserved.shape


def test_unclosed_or_unrecognized_sql_is_conservatively_redacted():
    result = sqlparse.redact("SELECT * FROM t WHERE name = 'Alice 9001")

    assert result.text.endswith("[REDACTED_UNPARSED]")
    assert "Alice" not in result.text
    assert "9001" not in result.text + result.shape + result.shape_id
    assert result.shape.endswith("<?>")


def test_redaction_options_are_used():
    options = sqlparse.RedactionOptions(
        string_marker="<string>", number_marker="<number>",
        boolean_marker="<bool>", in_list_marker="<values>")
    result = sqlparse.redact(
        "SELECT * FROM t WHERE name = 'Alice' AND amount = -1 "
        "AND active = TRUE AND id IN (1, 2, 3, 4, 5)", options=options)

    assert "<string>" in result.text
    assert "<number>" in result.text
    assert "<bool>" in result.text
    assert "(<values>)" in result.text
    assert result.shape == (
        "SELECT * FROM t WHERE name = ? AND amount = ? "
        "AND active = ? AND id IN (?)")


def test_literal_and_bind_placeholders_have_distinct_shape_markers():
    literal = sqlparse.redact("SELECT * FROM t WHERE id = 123")
    placeholder = sqlparse.redact("SELECT * FROM t WHERE id = :id")

    assert literal.shape.endswith("= ?")
    assert placeholder.shape.endswith("= :BIND")
    assert literal.shape_id != placeholder.shape_id


def test_tzcast_string_literal_is_redacted():
    result = sqlparse.redact(
        "SELECT * FROM t WHERE created AT TIME ZONE 'Alice/Secret'")

    assert "Alice" not in result.text + result.shape
    assert "Secret" not in result.text + result.shape
    assert result.shape.endswith("AT TIME ZONE '?'")


def test_invalid_options_are_rejected():
    with pytest.raises(ValueError):
        sqlparse.redact("SELECT 1", in_list_threshold=0)
    with pytest.raises(ValueError):
        sqlparse.redact("SELECT 1", hint_policy="remove")
    with pytest.raises(ValueError):
        sqlparse.redact(
            "SELECT 1",
            options=sqlparse.RedactionOptions(),
            string_marker=":lit")


def test_unclosed_dollar_quote_is_redacted():
    statement = "SELECT $body$Alice 9001"
    tokens = list(lexer.tokenize(statement))
    result = sqlparse.redact(statement)

    assert tokens[-1][0] is not T.Literal
    assert result.text.rstrip().endswith("[REDACTED_UNPARSED]")
    assert "Alice" not in result.text
    assert "9001" not in result.text + result.shape
