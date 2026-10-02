.. _analyze:

Analyzing the Parsed Statement
==============================

When the :meth:`~sqlparse.parse` function is called the returned value
is a tree-ish representation of the analyzed statements. The returned
objects can be used by applications to retrieve further information about
the parsed SQL.


Analyzing Incomplete Input
--------------------------

Applications that see SQL while it is still being typed, e.g. online
editors, can use :meth:`~sqlparse.analyze` for a fault-tolerant lexical
analysis.  It returns the tokens recognized so far together with
diagnostics for unterminated string literals, quoted names, block
comments and dollar-quoted literals.  The result keeps the original
text and each diagnostic points at the offset where the unterminated
construct starts.

The analysis distinguishes input that may still grow from finished
input.  While more input can be appended (the default,
``final=False``), an unterminated construct is reported as *pending*;
with ``final=True`` the same construct is an error:

.. code-block:: python

  >>> import sqlparse
  >>> result = sqlparse.analyze("SELECT * FROM t WHERE name = 'ada")
  >>> result.status
  'pending'
  >>> result.diagnostics[0].kind, result.diagnostics[0].start
  ('unclosed_string', 29)

Once the missing delimiter arrives, the diagnostic disappears:

.. code-block:: python

  >>> sqlparse.analyze("SELECT * FROM t WHERE name = 'ada'").status
  'ok'
  >>> sqlparse.analyze("SELECT 'ada", final=True).status
  'error'

Note that this is a lexical analysis only.  It does not check whether
the tokens form a valid SQL statement, and it never invents recovery
points: an unterminated construct extends to the end of the input, so
a semicolon inside an unclosed string is not mistaken for a statement
boundary.  Block comments do not nest; ``/*`` is closed by the first
following ``*/``.


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
