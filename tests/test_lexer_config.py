import threading

import pytest

import sqlparse
from sqlparse import tokens as T
from sqlparse.lexer import Lexer, LexerBuilder


CUSTOM_WORD = "bacon"


def test_builder_freezes_rules():
    custom_keywords = {CUSTOM_WORD: T.Name.Builtin}
    lexer = (
        LexerBuilder.default().add_keywords(custom_keywords).freeze()
    )
    custom_keywords[CUSTOM_WORD] = T.Keyword

    token = next(lexer.get_tokens(CUSTOM_WORD))

    assert lexer.frozen is True
    assert token == (T.Name.Builtin, CUSTOM_WORD)
    with pytest.raises(AttributeError):
        lexer.frozen = False
    with pytest.raises(AttributeError):
        lexer._keywords = ()


def test_derived_config_overrides_keywords_without_mutating_parent():
    parent = LexerBuilder.default().add_keyword(CUSTOM_WORD, T.Keyword).freeze()
    child = parent.derive().add_keyword(CUSTOM_WORD, T.Name.Builtin).freeze()

    assert next(parent.get_tokens(CUSTOM_WORD)) == (T.Keyword, CUSTOM_WORD)
    assert next(child.get_tokens(CUSTOM_WORD)) == (
        T.Name.Builtin,
        CUSTOM_WORD,
    )

    child_builder = child.derive().add_keyword(CUSTOM_WORD, T.Keyword)
    assert next(child.get_tokens(CUSTOM_WORD)) == (
        T.Name.Builtin,
        CUSTOM_WORD,
    )
    derived = child_builder.freeze()
    assert next(derived.get_tokens(CUSTOM_WORD)) == (T.Keyword, CUSTOM_WORD)


def test_regex_order_is_preserved_on_freeze():
    lexer = LexerBuilder.default().set_SQL_REGEX((
        (r"abc", T.Keyword),
        (r"abc", T.Name),
    )).freeze()

    assert lexer.sql_regex == ((r"abc", T.Keyword), (r"abc", T.Name))


def test_public_entry_points_require_frozen_lexer():
    builder = LexerBuilder.default()

    for call in (
        lambda: sqlparse.parse(CUSTOM_WORD, lexer=builder),
        lambda: sqlparse.split(CUSTOM_WORD, lexer=builder),
        lambda: sqlparse.format(CUSTOM_WORD, lexer=builder),
    ):
        with pytest.raises(TypeError):
            call()


def test_explicit_split_parse_and_format_use_separate_configurations():
    keyword_lexer = LexerBuilder.default().add_keyword(
        CUSTOM_WORD, T.Keyword
    ).freeze()
    name_lexer = LexerBuilder.default().add_keyword(
        CUSTOM_WORD, T.Name
    ).freeze()

    keyword_stmt = sqlparse.parse(CUSTOM_WORD, lexer=keyword_lexer)[0]
    name_stmt = sqlparse.parse(CUSTOM_WORD, lexer=name_lexer)[0]

    assert keyword_stmt.tokens[0].ttype is T.Keyword
    assert next(name_stmt.flatten()).ttype is T.Name
    assert sqlparse.split(
        f"{CUSTOM_WORD};",
        strip_semicolon=True,
        lexer=keyword_lexer,
    ) == [CUSTOM_WORD]
    assert sqlparse.format(
        CUSTOM_WORD.upper(),
        lexer=keyword_lexer,
        keyword_case="lower",
    ) == CUSTOM_WORD
    assert sqlparse.format(
        CUSTOM_WORD.upper(),
        lexer=name_lexer,
        identifier_case="lower",
    ) == CUSTOM_WORD


def test_concurrent_configurations_do_not_leak_between_threads():
    keyword_lexer = LexerBuilder.default().add_keyword(
        CUSTOM_WORD, T.Keyword
    ).freeze()
    name_lexer = LexerBuilder.default().add_keyword(
        CUSTOM_WORD, T.Name
    ).freeze()
    barrier = threading.Barrier(2)
    errors = []

    def worker(lexer, ttype, formatted):
        try:
            for _ in range(100):
                barrier.wait()
                statement = sqlparse.parse(
                    CUSTOM_WORD.upper(), lexer=lexer
                )[0]
                actual_ttype = next(statement.flatten()).ttype
                assert actual_ttype is ttype
                assert sqlparse.split(
                    f"{CUSTOM_WORD};",
                    strip_semicolon=True,
                    lexer=lexer,
                ) == [CUSTOM_WORD]
                case_option = (
                    "keyword_case" if ttype is T.Keyword else "identifier_case"
                )
                assert sqlparse.format(
                    CUSTOM_WORD.upper(),
                    lexer=lexer,
                    **{case_option: "lower"},
                ) == formatted
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(
            target=worker, args=(keyword_lexer, T.Keyword, CUSTOM_WORD)
        ),
        threading.Thread(
            target=worker, args=(name_lexer, T.Name, CUSTOM_WORD)
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    default_statement = sqlparse.parse(CUSTOM_WORD.upper())[0]
    assert next(default_statement.flatten()).ttype is T.Name
    assert sqlparse.format(CUSTOM_WORD.upper()) == CUSTOM_WORD.upper()


def test_default_instance_is_shared_and_frozen():
    assert Lexer.get_default_instance() is sqlparse.lexer.get_default_config()
    assert Lexer.get_default_instance().frozen is True
