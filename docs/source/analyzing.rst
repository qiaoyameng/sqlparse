.. _analyze:

Analyzing the Parsed Statement
==============================

When the :meth:`~sqlparse.parse` function is called the returned value
is a tree-ish representation of the analyzed statements. The returned
objects can be used by applications to retrieve further information about
the parsed SQL.


Base Classes
------------

All returned objects inherit from these base classes.
The :class:`~sqlparse.sql.Token` class represents a single token and
:class:`~sqlparse.sql.TokenList` class is a group of tokens.
The latter provides methods for inspecting its child tokens.

.. autoclass:: sqlparse.sql.Token
   :members:

.. autoclass:: sqlparse.sql.TokenList
   :members:


SQL Representing Classes
------------------------

The following classes represent distinct parts of a SQL statement.

.. autoclass:: sqlparse.sql.Statement
   :members:

.. autoclass:: sqlparse.sql.Comment
   :members:

.. autoclass:: sqlparse.sql.Identifier
   :members:

.. autoclass:: sqlparse.sql.IdentifierList
   :members:

.. autoclass:: sqlparse.sql.Where
   :members:

.. autoclass:: sqlparse.sql.Case
   :members:

.. autoclass:: sqlparse.sql.Parenthesis
   :members:

.. autoclass:: sqlparse.sql.If
   :members:

.. autoclass:: sqlparse.sql.For
   :members:

.. autoclass:: sqlparse.sql.Assignment
   :members:

.. autoclass:: sqlparse.sql.Comparison
   :members:


.. _incomplete-input:

Analyzing Incomplete Input
--------------------------

Online editors and similar tools often deal with SQL that is still being
typed. The :meth:`sqlparse.analyze` function is a fault-tolerant entry
point for such input: it returns the tokens together with lexical
diagnostics that explain unterminated string literals, block comments or
dollar-quoted literals. It does not attempt to validate the syntax.

Pass ``final=False`` while the user may still append more text. An
unterminated construct is then reported as *pending* instead of an
error:

.. code-block:: pycon

   >>> import sqlparse
   >>> result = sqlparse.analyze("select * from t where name = 'ada",
   ...                           final=False)
   >>> result.pending
   True
   >>> diagnostic = result.diagnostics[0]
   >>> diagnostic.kind, diagnostic.start, diagnostic.opener
   ('unclosed_string', 29, "'")
   >>> diagnostic.message
   'unterminated string literal opened by "\'" at offset 29; input may still be continued'

The original text is preserved as ``result.text`` and ``diagnostic.start``
points at the opening delimiter. The unterminated construct swallows the
rest of the input as a single token, so a semicolon inside it is never
mistaken for a statement boundary:

.. code-block:: pycon

   >>> result.tokens[-1]
   (Token.Literal.String.Single, "'ada")

Once the user types the closing delimiter, the diagnostic disappears:

.. code-block:: pycon

   >>> result = sqlparse.analyze("select * from t where name = 'ada'")
   >>> result.ok
   True
   >>> result.diagnostics
   []

With the default ``final=True`` the input is considered complete and the
same condition is reported as an error (``diagnostic.pending`` is
``False``). Note that block comments do not nest: the first ``*/``
closes the comment, no matter how many ``/*`` openers were seen before
it.

.. autoclass:: sqlparse.lexical.LexicalAnalysis
   :members:

.. autoclass:: sqlparse.lexical.Diagnostic
   :members:
