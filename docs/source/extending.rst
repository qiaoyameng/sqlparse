Extending :mod:`sqlparse`
=========================

.. module:: sqlparse
   :synopsis: Extending parsing capability of sqlparse.
   :no-index:

The :mod:`sqlparse` module uses a sql grammar that was tuned through usage and numerous
PR to fit a broad range of SQL syntaxes, but it cannot cater to every given case since
some SQL dialects have adopted conflicting meanings of certain keywords. Sqlparse
therefore exposes a mechanism to configure the fundamental keywords and regular
expressions that parse the language as described below.

If you find an adaptation that works for your specific use-case. Please consider
contributing it back to the community by opening a PR on
`GitHub <https://github.com/andialbrecht/sqlparse>`_.

Configuring a Lexer
---------------------

A lexer configuration breaks down the stream of characters into language tokens.
Configurations are immutable, while a mutable builder defines the sequence of regular
expressions and keyword dictionaries. Regex rules run in the order supplied to the
builder. Keyword dictionaries keep their existing first-match order, and dictionaries
added with ``override_keywords`` take precedence over all inherited dictionaries.

Start from the default configuration to support multiple database dialects in the same
process without replacing global state. Derive a builder, change only the rules that
differ, freeze the result with ``build()``, and pass that object to ``parse``,
``split``, or ``format``. When no configuration is supplied, those functions use the
default configuration.

See the expected types of the arguments by inspecting their structure in
``sqlparse.keywords``.

.. important::

   Building and parsing are separate phases. Do not change a builder while another
   thread is using a configuration built from it; freeze the configuration first.
   Calling ``build()`` copies the builder's rules, so later builder changes do not
   affect already-frozen configurations.

The following example recognizes ``ZORDER BY`` and treats ``BAR`` as a keyword:

..  code-block:: python

    import sqlparse
    from sqlparse import keywords
    from sqlparse.lexer import LexerConfigBuilder

    my_regex = (r"ZORDER\s+BY\b", sqlparse.tokens.Keyword)

    lexer = (
        LexerConfigBuilder.default()
        .set_sql_regex(
            keywords.SQL_REGEX[:38]
            + [my_regex]
            + keywords.SQL_REGEX[38:]
        )
        .override_keywords({'BAR': sqlparse.tokens.Keyword})
        .build()
    )

    sqlparse.parse("select * from foo zorder by bar;", lexer=lexer)

Derive a second configuration from another one without modifying the parent:

..  code-block:: python

    from sqlparse.lexer import LexerConfig

    postgresql = LexerConfig.default()
    snowflake = (
        postgresql.derive()
        .override_keywords({"QUALIFY": sqlparse.tokens.Keyword})
        .build()
    )

    sqlparse.split(sql, lexer=postgresql)
    sqlparse.format(sql, reindent=True, lexer=snowflake)
