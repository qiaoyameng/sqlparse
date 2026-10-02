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

Configuring the Lexer
---------------------

A lexer breaks down the stream of characters into language tokens. Each
:class:`~sqlparse.lexer.Lexer` instance is an immutable lexical configuration. Create
one with :class:`~sqlparse.lexer.LexerBuilder`, add rules in a predictable order, then
call ``freeze()``. Frozen configurations can be passed explicitly to
:func:`parse`, :func:`split`, and :func:`format`, allowing several SQL dialects to be
used in the same process without changing global state.

The default entry points continue to use the shared default configuration returned by
``Lexer.get_default_instance()``.

..  code-block:: python

    import sqlparse
    from sqlparse import tokens
    from sqlparse.lexer import LexerBuilder

    lexer = (
        LexerBuilder.default()
        .add_keyword('BAR', tokens.Keyword)
        .freeze()
    )

    sqlparse.parse('select bar', lexer=lexer)
    sqlparse.split('select bar;', lexer=lexer)
    sqlparse.format('select bar', keyword_case='upper', lexer=lexer)

Derived configurations start from an existing frozen lexer without mutating it. Layers
added on the derived builder take precedence over layers inherited from the parent.

..  code-block:: python

    parent = LexerBuilder.default().add_keyword('BAR', tokens.Name).freeze()
    child = parent.derive().add_keyword('BAR', tokens.Keyword).freeze()

    assert next(parent.get_tokens('bar'))[0] == tokens.Name
    assert next(child.get_tokens('bar'))[0] == tokens.Keyword

Regular expression rules can also be replaced before freezing. Use
``set_SQL_REGEX()`` with the ordered expressions in ``sqlparse.keywords``, or make a
copy of an existing frozen lexer's ``sql_regex`` and insert a new expression at the
required position.

..  code-block:: python

    from sqlparse import keywords, tokens

    zorder_regex = (r'ZORDER\s+BY\b', tokens.Keyword)
    lexer = (
        LexerBuilder.default()
        .set_SQL_REGEX(
            keywords.SQL_REGEX[:38]
            + [zorder_regex]
            + keywords.SQL_REGEX[38:]
        )
        .freeze()
    )

    sqlparse.parse('select * from foo zorder by bar;', lexer=lexer)
