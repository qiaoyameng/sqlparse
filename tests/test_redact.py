import pytest

import sqlparse
from sqlparse import DIAGNOSTIC_NOTICE, RedactionResult, RedactPolicy


def test_result_is_diagnostic():
    result = sqlparse.redact('SELECT 1')
    assert isinstance(result, RedactionResult)
    assert result.is_diagnostic is True
    assert result.notice == DIAGNOSTIC_NOTICE
    assert 'not executable' in result.notice


def test_string_with_escaped_quotes_masked():
    result = sqlparse.redact(
        "SELECT * FROM users WHERE name = 'O''Brien' "
        "AND note = 'it\\'s secret'")
    assert "O''Brien" not in result.text
    assert "O''Brien" not in result.shape
    assert "secret" not in result.text
    assert "secret" not in result.shape
    assert result.text == "SELECT * FROM users WHERE name = ? AND note = ?"


def test_dollar_quoted_literal_masked():
    result = sqlparse.redact(
        "SELECT $$customer Alice$$, $tag$amount 42$tag$ FROM t")
    for fragment in ('Alice', 'amount 42', '$$', '$tag$'):
        assert fragment not in result.text
        assert fragment not in result.shape
    assert result.text == 'SELECT ?, ? FROM t'


def test_negative_numbers_masked():
    result = sqlparse.redact(
        'SELECT * FROM t WHERE balance < -42.5 AND delta = -7')
    assert '42.5' not in result.text
    assert '42.5' not in result.shape
    assert '-7' not in result.text
    assert result.text == 'SELECT * FROM t WHERE balance < ? AND delta = ?'


def test_boolean_literals_masked():
    result = sqlparse.redact(
        'SELECT * FROM t WHERE active = TRUE AND deleted = false')
    assert 'TRUE' not in result.text
    assert 'false' not in result.text
    assert result.text == 'SELECT * FROM t WHERE active = ? AND deleted = ?'


def test_comments_removed_by_default():
    result = sqlparse.redact(
        'SELECT 1 /* customer: Alice, amount: 9.99 */ FROM t\n'
        '-- secret: Bob\n'
        'WHERE x = 1')
    for fragment in ('Alice', 'Bob', '9.99', 'secret', 'customer'):
        assert fragment not in result.text
        assert fragment not in result.shape
    assert '/*' not in result.text
    assert result.text == 'SELECT ? FROM t WHERE x = ?'


def test_comment_mask_policy():
    policy = RedactPolicy(comment_policy='mask')
    result = sqlparse.redact('SELECT 1 /* Alice */ FROM t', policy=policy)
    assert 'Alice' not in result.text
    assert 'Alice' not in result.shape
    assert '/*REDACTED*/' in result.text


def test_hint_masked_by_default():
    result = sqlparse.redact(
        'SELECT /*+ INDEX(t secret_idx) */ 1 FROM t')
    assert 'secret_idx' not in result.text
    assert 'secret_idx' not in result.shape
    assert '/*+REDACTED*/' in result.text


def test_hint_keep_policy_keeps_display_but_not_shape():
    policy = RedactPolicy(hint_policy='keep')
    result = sqlparse.redact(
        'SELECT /*+ INDEX(t secret_idx) */ 1 FROM t', policy=policy)
    assert '/*+ INDEX(t secret_idx) */' in result.text
    assert 'secret_idx' not in result.shape
    assert '/*+*/' in result.shape


def test_hint_remove_policy():
    policy = RedactPolicy(hint_policy='remove')
    result = sqlparse.redact(
        'SELECT /*+ INDEX(t secret_idx) */ 1 FROM t', policy=policy)
    assert 'secret_idx' not in result.text
    assert '/*+' not in result.text
    assert result.text == 'SELECT ? FROM t'


def test_single_line_hint_is_hint_not_comment():
    result = sqlparse.redact('SELECT 1 --+ secret hint\nFROM t')
    assert 'secret hint' not in result.text
    assert '/*+REDACTED*/' in result.text


def test_placeholders_kept_separate_from_literals():
    result = sqlparse.redact(
        'SELECT * FROM t WHERE a = ? AND b = %s AND c = :name AND d = $1')
    assert '?' in result.text
    assert '%s' in result.text
    assert ':name' in result.text
    assert '$1' in result.text
    # A bound parameter and a literal must not fall into the same group.
    literal = sqlparse.redact('SELECT * FROM t WHERE a = 5')
    param = sqlparse.redact('SELECT * FROM t WHERE a = ?')
    assert literal.fingerprint != param.fingerprint


def test_placeholder_mask_option():
    policy = RedactPolicy(placeholder_mask='<param>')
    result = sqlparse.redact('SELECT * FROM t WHERE a = :secret_name',
                             policy=policy)
    assert 'secret_name' not in result.text
    assert '<param>' in result.text


def test_placeholders_normalized_in_shape():
    first = sqlparse.redact('SELECT * FROM t WHERE a = $1')
    second = sqlparse.redact('SELECT * FROM t WHERE a = $2')
    assert first.fingerprint == second.fingerprint


def test_large_in_list_collapsed():
    result = sqlparse.redact(
        'SELECT * FROM t WHERE id IN (101, 202, 303, 404, 505, 606)')
    assert result.text == 'SELECT * FROM t WHERE id IN (?)'
    for fragment in ('101', '202', '303', '404', '505', '606'):
        assert fragment not in result.text
        assert fragment not in result.shape


def test_small_in_list_masked_itemwise():
    result = sqlparse.redact('SELECT * FROM t WHERE id IN (101, 202)')
    assert result.text == 'SELECT * FROM t WHERE id IN (?, ?)'


def test_in_list_threshold_configurable():
    policy = RedactPolicy(in_list_threshold=2)
    result = sqlparse.redact('SELECT * FROM t WHERE id IN (101, 202)',
                             policy=policy)
    assert result.text == 'SELECT * FROM t WHERE id IN (?)'
    policy = RedactPolicy(in_list_threshold=None)
    result = sqlparse.redact(
        'SELECT * FROM t WHERE id IN (1, 2, 3, 4, 5, 6)', policy=policy)
    assert result.text == 'SELECT * FROM t WHERE id IN (?, ?, ?, ?, ?, ?)'


def test_in_list_with_subquery_not_collapsed():
    result = sqlparse.redact(
        'SELECT * FROM t WHERE id IN (SELECT id FROM u WHERE x = 5)')
    assert 'SELECT id FROM u WHERE x = ?' in result.text


def test_in_list_with_string_items_collapsed():
    result = sqlparse.redact(
        "SELECT * FROM t WHERE name IN ('a1', 'b2', 'c3', 'd4', 'e5')")
    assert result.text == 'SELECT * FROM t WHERE name IN (?)'
    for fragment in ('a1', 'b2', 'c3', 'd4', 'e5'):
        assert fragment not in result.text


def test_same_group_for_literal_changes_only():
    first = sqlparse.redact(
        "SELECT * FROM orders WHERE customer = 'Alice' AND amount = 100")
    second = sqlparse.redact(
        "SELECT * FROM orders WHERE customer = 'Bob' AND amount = 250.5")
    assert first.shape == second.shape
    assert first.fingerprint == second.fingerprint


def test_same_group_independent_of_keyword_case_and_whitespace():
    first = sqlparse.redact('select * from t where a = 1')
    second = sqlparse.redact('SELECT  *  FROM t WHERE a = 2')
    assert first.fingerprint == second.fingerprint


def test_different_groups_for_different_shapes():
    first = sqlparse.redact('SELECT a FROM t WHERE x = 1')
    second = sqlparse.redact('SELECT a, b FROM t WHERE x = 1')
    assert first.fingerprint != second.fingerprint


def test_unrecognized_fragment_masked_conservatively():
    result = sqlparse.redact('SELECT `unclosed')
    assert '`' not in result.text
    assert '`' not in result.shape
    assert '<unrecognized>' in result.text


def test_custom_masks():
    policy = RedactPolicy(string_mask='<str>', number_mask='<num>',
                          boolean_mask='<bool>')
    result = sqlparse.redact(
        "SELECT * FROM t WHERE a = 'x' AND b = 3 AND c = TRUE",
        policy=policy)
    assert result.text == (
        'SELECT * FROM t WHERE a = <str> AND b = <num> AND c = <bool>')


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        RedactPolicy(comment_policy='keep')
    with pytest.raises(ValueError):
        RedactPolicy(hint_policy='strip')
    with pytest.raises(ValueError):
        RedactPolicy(in_list_threshold=1)


def test_empty_input():
    result = sqlparse.redact('')
    assert result.text == ''
    assert result.shape == ''
    assert result.fingerprint


def test_multiple_statements():
    result = sqlparse.redact("SELECT 'a'; SELECT 'b';")
    assert result.text == 'SELECT ?; SELECT ?;'
    first = sqlparse.redact('SELECT 1;')
    second = sqlparse.redact('SELECT 2;')
    assert first.fingerprint == second.fingerprint


def test_keyword_with_embedded_literal_masked():
    result = sqlparse.redact(
        "SELECT '2024-01-01'::timestamptz AT TIME ZONE 'Europe/Berlin'")
    assert 'Europe/Berlin' not in result.text
    assert 'Europe/Berlin' not in result.shape
    assert 'AT TIME ZONE ?' in result.text


def test_fingerprint_is_stable_hexdigest():
    result = sqlparse.redact('SELECT 1')
    assert len(result.fingerprint) == 64
    int(result.fingerprint, 16)
    again = sqlparse.redact('SELECT 9')
    assert result.fingerprint == again.fingerprint
