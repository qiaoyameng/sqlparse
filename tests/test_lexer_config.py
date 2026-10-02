from threading import Barrier, Thread

import pytest

import sqlparse
from sqlparse import tokens as T
from sqlparse.engine import FilterStack, grouping
from sqlparse.lexer import Lexer, LexerConfig, LexerConfigBuilder


def test_build_freezes_rules():
    source = {"BACON": T.Name}
    builder = LexerConfigBuilder.default().add_keywords(source)
    config = builder.build()

    source["BACON"] = T.Keyword
    builder.clear()

    token_type, value = next(config.get_tokens("bacon"))

    assert value == "bacon"
    assert token_type is T.Name

    with pytest.raises(AttributeError):
        config._keyword_layers = ()

    with pytest.raises(TypeError):
        config._keyword_layers[0]["BACON"] = T.Keyword

    with pytest.raises(AttributeError):
        config._sql_regex = ()

    with pytest.raises(TypeError):
        config._sql_regex[0] = config._sql_regex[-1]


def test_derived_config_overrides_parent_without_mutating_it():
    parent = (
        LexerConfigBuilder.default()
        .add_keywords({"BACON": T.Keyword})
        .build()
    )

    child = parent.derive().override_keywords({"BACON": T.Name}).build()

    assert next(parent.get_tokens("BACON")) == (T.Keyword, "BACON")
    assert next(child.get_tokens("BACON")) == (T.Name, "BACON")

    added_child = (
        parent.derive()
        .add_keywords({"BACON": T.Name.Builtin})
        .build()
    )
    assert next(added_child.get_tokens("BACON")) == (T.Name.Builtin, "BACON")
    assert next(parent.get_tokens("BACON")) == (T.Keyword, "BACON")

    later_override = (
        parent.derive()
        .override_keywords({"BACON": T.Name.Builtin})
        .override_keywords({"BACON": T.Name})
        .build()
    )
    assert next(later_override.get_tokens("BACON")) == (T.Name, "BACON")


def test_parse_split_and_format_accept_lexer_config():
    keyword_config = (
        LexerConfig.default()
        .derive()
        .override_keywords({"BACON": T.Keyword})
        .build()
    )
    name_config = (
        LexerConfig.default()
        .derive()
        .override_keywords({"BACON": T.Name.Builtin})
        .build()
    )

    parsed = sqlparse.parse("select bacon from t;", lexer=keyword_config)[0]
    assert parsed.tokens[0].ttype is T.Keyword.DML
    assert parsed.tokens[2].ttype is T.Keyword

    parsed = sqlparse.parse("select bacon from t;", lexer=name_config)[0]
    assert parsed.tokens[2].ttype is T.Name.Builtin

    assert sqlparse.split(
        "select bacon from t; select bacon from u;", lexer=keyword_config
    ) == ["select bacon from t;", "select bacon from u;"]

    formatted = sqlparse.format(
        "select bacon from t;",
        keyword_case="upper",
        lexer=keyword_config,
    )
    assert formatted == "SELECT BACON FROM t;"


def test_filter_stack_uses_supplied_lexer(monkeypatch):
    config = LexerConfig.default()
    original_group = grouping.group
    seen = []

    def capture_group(stmt, lexer):
        seen.append(lexer)
        return original_group(stmt, lexer)

    monkeypatch.setattr(grouping, "group", capture_group)

    def fail_default():
        raise AssertionError("default lexer must not be used")

    monkeypatch.setattr(Lexer, "get_default_instance", fail_default)

    stack = FilterStack(lexer=config)
    assert stack.lexer is config
    stack.enable_grouping()
    assert list(stack.run("select 1"))

    assert seen == [config]


@pytest.mark.parametrize(
    "api",
    [sqlparse.parse, sqlparse.split, sqlparse.format],
)
def test_entry_points_reject_invalid_lexer(api):
    with pytest.raises(TypeError):
        api("select 1", lexer=object())


def test_concurrent_custom_configs_do_not_leak():
    configs = {
        T.Keyword: (
            LexerConfig.default()
            .derive()
            .override_keywords({"BACON": T.Keyword})
            .build()
        ),
        T.Name.Builtin: (
            LexerConfig.default()
            .derive()
            .override_keywords({"BACON": T.Name.Builtin})
            .build()
        ),
    }
    iterations = 100
    expected_formatted = {
        T.Keyword: "SELECT BACON FROM t;",
        T.Name.Builtin: "SELECT bacon FROM t;",
    }
    barrier = Barrier(4)
    errors = []

    def worker(token_type, use_format):
        config = configs[token_type]
        barrier.wait()
        try:
            for _ in range(iterations):
                if use_format:
                    formatted = sqlparse.format(
                        "select bacon from t;",
                        keyword_case="upper",
                        lexer=config,
                    )
                    assert formatted == expected_formatted[token_type]
                else:
                    statement = sqlparse.parse(
                        "select bacon from t;", lexer=config
                    )[0]
                    token = statement.tokens[2]
                    assert token.ttype is token_type
        except BaseException as exc:
            errors.append(exc)

    threads = [
        Thread(target=worker, args=(T.Keyword, False)),
        Thread(target=worker, args=(T.Keyword, True)),
        Thread(target=worker, args=(T.Name.Builtin, False)),
        Thread(target=worker, args=(T.Name.Builtin, True)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []

    parsed = sqlparse.parse("select bacon from t;")[0]
    token = next(
        token
        for token in parsed.flatten()
        if token.value == "bacon"
    )
    assert token.ttype is T.Name
