import re

from sqlparse import tokens as T


PROTECTED_TTYPE = T.Other.Protected

_SINGLE_DIRECTIVE = re.compile(
    r'^(?:--|#)[ \t]*fmt[ \t]*:[ \t]*(off|on)[ \t]*'
    r'(?:\r\n|\r|\n|)$',
    re.IGNORECASE,
)
_MULTILINE_DIRECTIVE = re.compile(
    r'^/\*[\s]*fmt[\s]*:[\s]*(off|on)[\s]*\*/$',
    re.IGNORECASE,
)


class ProtectedRegionFilter:
    """Keep text enclosed by formatting directives unchanged."""

    @staticmethod
    def _directive(ttype, value):
        if ttype is T.Comment.Single:
            match = _SINGLE_DIRECTIVE.match(value)
        elif ttype is T.Comment.Multiline:
            match = _MULTILINE_DIRECTIVE.match(value)
        else:
            match = None

        if match is None:
            return None
        return match.group(1).lower()

    def process(self, stream):
        depth = 0
        pending = []

        for ttype, value in stream:
            directive = self._directive(ttype, value)

            if depth:
                if directive is None:
                    pending.append(value)
                    continue

                if directive == 'off':
                    depth += 1
                    pending.append(value)
                    continue

                elif directive == 'on':
                    if depth > 1:
                        pending.append(value)
                    else:
                        yield PROTECTED_TTYPE, ''.join(pending)
                        pending = []
                    depth -= 1
                    if depth == 0:
                        yield ttype, value
                continue

            if directive == 'off':
                depth = 1
                yield ttype, value
            else:
                yield ttype, value

        if pending:
            yield PROTECTED_TTYPE, ''.join(pending)
