#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""SQL lexer and lexer configuration."""

import re
from io import TextIOBase
from threading import Lock
from types import MappingProxyType

from sqlparse import keywords, tokens
from sqlparse.utils import consume


class LexerConfig:
    """Immutable lexical configuration used by a lexer."""

    __slots__ = (
        "_sql_regex",
        "_compiled_sql_regex",
        "_keyword_layers",
    )

    def __init__(self, sql_regex, keyword_layers):
        flags = re.IGNORECASE | re.UNICODE
        object.__setattr__(self, "_sql_regex", tuple(sql_regex))
        object.__setattr__(
            self,
            "_compiled_sql_regex",
            tuple(
                (re.compile(pattern, flags).match, action)
                for pattern, action in self._sql_regex
            ),
        )
        object.__setattr__(
            self,
            "_keyword_layers",
            tuple(
                MappingProxyType(dict(layer))
                for layer in keyword_layers
            ),
        )

    def __delattr__(self, name):
        raise AttributeError(f"Cannot delete frozen attribute {name!r}")

    def __setattr__(self, name, value):
        raise AttributeError(f"Cannot modify frozen attribute {name!r}")

    @property
    def _SQL_REGEX(self):
        return self._compiled_sql_regex

    @property
    def _keywords(self):
        keyword_types = {}
        for layer in reversed(self._keyword_layers):
            keyword_types.update(layer)
        return MappingProxyType(keyword_types)

    @classmethod
    def default(cls):
        return LexerConfigBuilder.default().build()

    def derive(self):
        return LexerConfigBuilder.from_config(self)

    def get_tokens(self, text, encoding=None):
        """Yield ``(token_type, value)`` pairs for ``text``."""
        if isinstance(text, TextIOBase):
            text = text.read()

        if isinstance(text, str):
            pass
        elif isinstance(text, bytes):
            if encoding:
                text = text.decode(encoding)
            else:
                try:
                    text = text.decode('utf-8')
                except UnicodeDecodeError:
                    text = text.decode('unicode-escape')
        else:
            raise TypeError(
                f"Expected text or file-like object, got {type(text)!r}"
            )

        delimited_spans = keywords.find_delimited_spans(text)
        span_openers = delimited_spans.openers

        iterable = enumerate(text)
        for pos, char in iterable:
            if pos in span_openers:
                resolved = delimited_spans.resolve(pos)
                if resolved is not None:
                    end, token_type = resolved
                    yield token_type, text[pos:end]
                    consume(iterable, end - pos - 1)
                    continue

            for regex_match, action in self._compiled_sql_regex:
                match = regex_match(text, pos)

                if not match:
                    continue
                elif isinstance(action, tokens._TokenType):
                    yield action, match.group()
                elif action is keywords.PROCESS_AS_KEYWORD:
                    yield self.is_keyword(match.group())

                consume(iterable, match.end() - pos - 1)
                break
            else:
                yield tokens.Error, char

    def is_keyword(self, value):
        """Return the configured token type for ``value``."""
        keyword = value.upper()
        for layer in self._keyword_layers:
            if keyword in layer:
                return layer[keyword], value
        return tokens.Name, value


class LexerConfigBuilder:
    """Build lexical configurations in a deterministic order."""

    def __init__(self):
        self.clear()

    @classmethod
    def default(cls):
        builder = cls()
        builder.set_sql_regex(keywords.SQL_REGEX)
        builder.add_keywords(keywords.KEYWORDS_COMMON)
        builder.add_keywords(keywords.KEYWORDS_ORACLE)
        builder.add_keywords(keywords.KEYWORDS_MYSQL)
        builder.add_keywords(keywords.KEYWORDS_PLPGSQL)
        builder.add_keywords(keywords.KEYWORDS_HQL)
        builder.add_keywords(keywords.KEYWORDS_MSACCESS)
        builder.add_keywords(keywords.KEYWORDS_SNOWFLAKE)
        builder.add_keywords(keywords.KEYWORDS_BIGQUERY)
        builder.add_keywords(keywords.KEYWORDS)
        return builder

    @classmethod
    def from_config(cls, config):
        builder = cls()
        builder._sql_regex = list(config._sql_regex)
        builder._inherited_keyword_layers = list(config._keyword_layers)
        return builder

    def add_keywords(self, keyword_layer):
        """Append a keyword layer. Earlier layers take precedence."""
        self._custom_keyword_layers.append(dict(keyword_layer))
        return self

    def build(self, config_class=LexerConfig):
        """Freeze the accumulated rules into a lexer configuration."""
        return config_class(
            tuple(self._sql_regex),
            tuple(reversed(self._override_keyword_layers))
            + tuple(self._custom_keyword_layers)
            + tuple(self._inherited_keyword_layers),
        )

    def freeze(self):
        """Freeze the accumulated rules into a lexer configuration."""
        return self.build()

    def clear(self):
        """Remove every regex and keyword layer from this builder."""
        self._sql_regex = []
        self._inherited_keyword_layers = []
        self._custom_keyword_layers = []
        self._override_keyword_layers = []
        return self

    def default_initialization(self):
        """Replace the builder's contents with the default rules."""
        self.clear()
        self.set_sql_regex(keywords.SQL_REGEX)
        self.add_keywords(keywords.KEYWORDS_COMMON)
        self.add_keywords(keywords.KEYWORDS_ORACLE)
        self.add_keywords(keywords.KEYWORDS_MYSQL)
        self.add_keywords(keywords.KEYWORDS_PLPGSQL)
        self.add_keywords(keywords.KEYWORDS_HQL)
        self.add_keywords(keywords.KEYWORDS_MSACCESS)
        self.add_keywords(keywords.KEYWORDS_SNOWFLAKE)
        self.add_keywords(keywords.KEYWORDS_BIGQUERY)
        self.add_keywords(keywords.KEYWORDS)
        return self

    def override_keywords(self, keyword_layer):
        """Add keywords that override all existing keyword layers."""
        self._override_keyword_layers.append(dict(keyword_layer))
        return self

    def set_sql_regex(self, sql_regex):
        """Replace the regex rules, preserving the supplied order."""
        self._sql_regex = list(sql_regex)
        return self

    def set_SQL_REGEX(self, SQL_REGEX):
        """Compatibility alias for :meth:`set_sql_regex`."""
        return self.set_sql_regex(SQL_REGEX)


class Lexer(LexerConfig):
    """Backwards-compatible name for a lexical configuration."""

    _default_instance = None
    _lock = Lock()

    def __init__(self, sql_regex=(), keyword_layers=()):
        super().__init__(sql_regex, keyword_layers)

    @classmethod
    def get_default_instance(cls):
        """Return the immutable configuration used by default entry points."""
        with cls._lock:
            if cls._default_instance is None:
                builder = LexerConfigBuilder.default()
                cls._default_instance = builder.build(cls)
            return cls._default_instance


LexerBuilder = LexerConfigBuilder


def tokenize(sql, encoding=None, *, lexer=None):
    """Tokenize *sql* using *lexer*, or default lexical configuration."""
    if lexer is None:
        lexer = Lexer.get_default_instance()
    elif not isinstance(lexer, LexerConfig):
        raise TypeError("lexer must be a LexerConfig instance")
    return lexer.get_tokens(sql, encoding)
