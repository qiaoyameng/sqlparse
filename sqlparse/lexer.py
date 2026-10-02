#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""SQL Lexer"""
import re
from collections.abc import Mapping
from io import TextIOBase
from threading import Lock
from types import MappingProxyType

# This code is based on the SqlLexer in pygments.
# http://pygments.org/
# It's separated from the rest of pygments to increase performance
# and to allow some customizations.

from sqlparse import keywords, tokens
from sqlparse.utils import consume


class Lexer:
    """Immutable lexical configuration used by a parse or format call."""

    __slots__ = ('_SQL_REGEX', '_raw_SQL_REGEX', '_keywords', 'frozen')

    _default_instance = None
    _default_instance_lock = Lock()

    def __init__(self, sql_regex, keyword_dicts):
        raw_regex = tuple(sql_regex)
        compiled_regex = []
        flags = re.IGNORECASE | re.UNICODE
        try:
            for rx, token_type in raw_regex:
                compiled_regex.append(
                    (re.compile(rx, flags).match, token_type)
                )
        except (TypeError, ValueError, re.error) as err:
            raise ValueError('Invalid SQL lexical regular expression') from err

        object.__setattr__(self, '_SQL_REGEX', tuple(compiled_regex))
        object.__setattr__(self, '_raw_SQL_REGEX', raw_regex)
        object.__setattr__(
            self,
            '_keywords',
            tuple(
                MappingProxyType(dict(keyword_dict))
                for keyword_dict in keyword_dicts
            ),
        )
        object.__setattr__(self, 'frozen', True)

    def __setattr__(self, name, value):
        raise AttributeError('Lexer configuration is frozen')

    def __delattr__(self, name):
        raise AttributeError('Lexer configuration is frozen')

    @property
    def sql_regex(self):
        return self._raw_SQL_REGEX

    @classmethod
    def get_default_instance(cls):
        """Return the immutable configuration used by default entry points."""
        with cls._default_instance_lock:
            if cls._default_instance is None:
                cls._default_instance = LexerBuilder.default().freeze()
            return cls._default_instance

    @classmethod
    def default(cls):
        """Return a builder containing the default lexical rules."""
        return LexerBuilder.default()

    @classmethod
    def builder(cls):
        """Return a builder containing the default lexical rules."""
        return LexerBuilder.default()

    def derive(self):
        """Return a builder initialized with this configuration."""
        return LexerBuilder.from_config(self)

    def freeze(self):
        return self

    def is_keyword(self, value):
        """Checks for a keyword.

        If the given value is in one of the KEYWORDS_* dictionary
        it's considered a keyword. Otherwise, tokens.Name is returned.
        """
        val = value.upper()
        for kwdict in self._keywords:
            if val in kwdict:
                return kwdict[val], value
        else:
            return tokens.Name, value

    def get_tokens(self, text, encoding=None):
        """Return an iterable of ``(tokentype, value)`` pairs."""
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
            raise TypeError(f"Expected text or file-like object, got {type(text)!r}")

        delimited_spans = keywords.find_delimited_spans(text)
        span_openers = delimited_spans.openers

        iterable = enumerate(text)
        for pos, char in iterable:
            if pos in span_openers:
                resolved = delimited_spans.resolve(pos)
                if resolved is not None:
                    end, ttype = resolved
                    yield ttype, text[pos:end]
                    consume(iterable, end - pos - 1)
                    continue

            for rexmatch, action in self._SQL_REGEX:
                m = rexmatch(text, pos)

                if not m:
                    continue
                elif isinstance(action, tokens._TokenType):
                    yield action, m.group()
                elif action is keywords.PROCESS_AS_KEYWORD:
                    yield self.is_keyword(m.group())

                consume(iterable, m.end() - pos - 1)
                break
            else:
                yield tokens.Error, char


class LexerBuilder:
    """Build lexical rules in a predictable order and freeze them."""

    def __init__(self):
        self._sql_regex = []
        self._keyword_dicts = []
        self._base_config = None

    @classmethod
    def default(cls):
        builder = cls()
        builder.set_SQL_REGEX(keywords.SQL_REGEX)
        for keyword_dict in (
            keywords.KEYWORDS_COMMON,
            keywords.KEYWORDS_ORACLE,
            keywords.KEYWORDS_MYSQL,
            keywords.KEYWORDS_PLPGSQL,
            keywords.KEYWORDS_HQL,
            keywords.KEYWORDS_MSACCESS,
            keywords.KEYWORDS_SNOWFLAKE,
            keywords.KEYWORDS_BIGQUERY,
            keywords.KEYWORDS,
        ):
            builder.add_keywords(keyword_dict)
        return builder

    @classmethod
    def from_config(cls, config):
        if not isinstance(config, Lexer):
            raise TypeError('Expected a frozen LexerConfig instance')
        builder = cls()
        builder._sql_regex = list(config.sql_regex)
        builder._base_config = config
        return builder

    def clear(self):
        self._sql_regex = []
        self._keyword_dicts = []
        self._base_config = None
        return self

    def set_SQL_REGEX(self, sql_regex):
        """Set the ordered list of regular expressions that tokenizes SQL."""
        self._sql_regex = list(sql_regex)
        return self

    def add_keywords(self, keyword_dict):
        """Add keywords ahead of the layers inherited from the base config."""
        if not isinstance(keyword_dict, Mapping):
            raise TypeError('Keywords must be provided as a mapping')
        self._keyword_dicts.append({
            keyword.upper(): token_type
            for keyword, token_type in keyword_dict.items()
        })
        return self

    def add_keyword(self, keyword, token_type):
        return self.add_keywords({keyword: token_type})

    def freeze(self):
        keyword_dicts = list(self._keyword_dicts)
        if self._base_config is not None:
            keyword_dicts.extend(self._base_config._keywords)
        return Lexer(self._sql_regex, keyword_dicts)


LexerConfig = Lexer
LexerConfigBuilder = LexerBuilder


def get_default_config():
    """Return the immutable lexer configuration used by default."""
    return Lexer.get_default_instance()


def resolve_lexer(lexer):
    """Return the frozen configuration for a parse or format call."""
    if lexer is None:
        return get_default_config()
    if not isinstance(lexer, Lexer):
        raise TypeError('lexer must be a frozen LexerConfig instance')
    return lexer


def tokenize(sql, encoding=None, *, lexer=None):
    """Tokenize sql.

    Tokenize *sql* using the :class:`Lexer` and return a 2-tuple stream
    of ``(token type, value)`` items.
    """
    lexer = resolve_lexer(lexer)
    return lexer.get_tokens(sql, encoding)
