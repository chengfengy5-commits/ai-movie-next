"""Conservative canonicalization for reflected CHECK expressions.

This parser intentionally supports only the grammar used by frozen generation
schema checks. Unknown SQL remains incomparable so readiness fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias


Expression: TypeAlias = tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str


class _UnsupportedExpression(ValueError):
    pass


class _Parser:
    def __init__(self, tokens: tuple[_Token, ...]) -> None:
        self._tokens = tokens
        self._position = 0

    def parse(self) -> Expression:
        expression = self._parse_or()
        if self._position != len(self._tokens):
            raise _UnsupportedExpression
        return expression

    def _parse_or(self) -> Expression:
        expression = self._parse_and()
        while self._accept_word("or"):
            expression = ("or", expression, self._parse_and())
        return expression

    def _parse_and(self) -> Expression:
        expression = self._parse_not()
        while self._accept_word("and"):
            expression = ("and", expression, self._parse_not())
        return expression

    def _parse_not(self) -> Expression:
        if self._accept_word("not"):
            return ("not", self._parse_not())
        return self._parse_predicate()

    def _parse_predicate(self) -> Expression:
        left = self._parse_additive()
        if self._accept_word("is"):
            negated = self._accept_word("not")
            if not self._accept_word("null"):
                raise _UnsupportedExpression
            return ("is_not_null" if negated else "is_null", left)

        negated = False
        if self._word_is("not") and self._word_is("in", offset=1):
            self._position += 1
            negated = True
        if self._accept_word("in"):
            values = self._parse_value_list()
            return ("not_in" if negated else "in", left, values)
        if negated:
            raise _UnsupportedExpression

        operator = self._accept_comparison()
        if operator is None:
            return left
        if self._accept_word("any"):
            self._expect("(")
            right = self._parse_or()
            self._expect(")")
            right = ("any", right)
        else:
            right = self._parse_additive()
        return ("compare", operator, left, right)

    def _parse_value_list(self) -> tuple[Expression, ...]:
        self._expect("(")
        values = [self._parse_additive()]
        while self._accept(","):
            values.append(self._parse_additive())
        self._expect(")")
        return tuple(values)

    def _parse_additive(self) -> Expression:
        expression = self._parse_multiplicative()
        while True:
            operator = self._accept_operator("+", "-")
            if operator is None:
                return expression
            expression = ("arithmetic", operator, expression, self._parse_multiplicative())

    def _parse_multiplicative(self) -> Expression:
        expression = self._parse_unary()
        while True:
            operator = self._accept_operator("*", "/")
            if operator is None:
                return expression
            expression = ("arithmetic", operator, expression, self._parse_unary())

    def _parse_unary(self) -> Expression:
        operator = self._accept_operator("+", "-")
        if operator is not None:
            return ("unary", operator, self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Expression:
        if self._accept("("):
            expression = self._parse_or()
            self._expect(")")
            return self._parse_casts(expression)

        if self._accept_word("array"):
            self._expect("[")
            values: list[Expression] = []
            if not self._accept("]"):
                values.append(self._parse_additive())
                while self._accept(","):
                    values.append(self._parse_additive())
                self._expect("]")
            return self._parse_casts(("array", tuple(values)))

        token = self._take()
        if token is None:
            raise _UnsupportedExpression
        if token.kind == "string":
            return self._parse_casts(("string", token.value))
        if token.kind == "number":
            return self._parse_casts(("number", token.value))
        if token.kind not in {"identifier", "quoted_identifier"}:
            raise _UnsupportedExpression

        if token.kind == "identifier" and token.value.casefold() == "null":
            return self._parse_casts(("null",))
        if token.kind == "identifier" and token.value.casefold() in {"true", "false"}:
            return self._parse_casts(("boolean", token.value.casefold() == "true"))

        parts = [token.value]
        while self._accept("."):
            next_part = self._take()
            if next_part is None or next_part.kind not in {"identifier", "quoted_identifier"}:
                raise _UnsupportedExpression
            parts.append(next_part.value)
        if self._accept("("):
            arguments: list[Expression] = []
            if not self._accept(")"):
                arguments.append(self._parse_or())
                while self._accept(","):
                    arguments.append(self._parse_or())
                self._expect(")")
            expression = ("call", tuple(parts), tuple(arguments))
        else:
            expression = ("identifier", tuple(parts))
        return self._parse_casts(expression)

    def _parse_casts(self, expression: Expression) -> Expression:
        while self._accept("::"):
            expression = ("cast", expression, self._parse_type())
        return expression

    def _parse_type(self) -> tuple[object, ...]:
        first = self._take()
        if first is None or first.kind not in {"identifier", "quoted_identifier"}:
            raise _UnsupportedExpression
        parts = [first.value]
        if self._accept("."):
            second = self._take()
            if second is None or second.kind not in {"identifier", "quoted_identifier"}:
                raise _UnsupportedExpression
            parts.append(second.value)
        if len(parts) == 1 and first.kind == "identifier" and parts[0] == "character":
            if self._accept_word("varying"):
                parts[0] = "character varying"
        elif len(parts) == 1 and first.kind == "identifier" and parts[0] == "double":
            if self._accept_word("precision"):
                parts[0] = "double precision"

        modifier: tuple[str, ...] = ()
        if self._accept("("):
            modifier_values = [self._take()]
            while self._accept(","):
                modifier_values.append(self._take())
            self._expect(")")
            if any(token is None or token.kind != "number" for token in modifier_values):
                raise _UnsupportedExpression
            modifier = tuple(token.value for token in modifier_values if token is not None)

        array_dimensions = 0
        while self._accept("["):
            self._expect("]")
            array_dimensions += 1
        name = ".".join(parts)
        return ("type", name, modifier, array_dimensions)

    def _accept_comparison(self) -> str | None:
        token = self._peek()
        if token is None or token.kind != "operator" or token.value not in {
            "=", "!=", "<>", "<", "<=", ">", ">="
        }:
            return None
        self._position += 1
        return "!=" if token.value == "<>" else token.value

    def _accept_operator(self, *operators: str) -> str | None:
        token = self._peek()
        if token is None or token.kind != "operator" or token.value not in operators:
            return None
        self._position += 1
        return token.value

    def _accept_word(self, value: str) -> bool:
        if not self._word_is(value):
            return False
        self._position += 1
        return True

    def _word_is(self, value: str, *, offset: int = 0) -> bool:
        position = self._position + offset
        if position >= len(self._tokens):
            return False
        token = self._tokens[position]
        return token.kind == "identifier" and token.value.casefold() == value

    def _accept(self, value: str) -> bool:
        token = self._peek()
        if token is None or token.value != value:
            return False
        self._position += 1
        return True

    def _expect(self, value: str) -> None:
        if not self._accept(value):
            raise _UnsupportedExpression

    def _take(self) -> _Token | None:
        token = self._peek()
        if token is not None:
            self._position += 1
        return token

    def _peek(self) -> _Token | None:
        return self._tokens[self._position] if self._position < len(self._tokens) else None


def _tokenize(expression: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    index = 0
    while index < len(expression):
        character = expression[index]
        if character.isspace():
            index += 1
            continue
        if character == "'":
            value, index = _read_quoted(expression, index + 1, "'")
            tokens.append(_Token("string", value))
            continue
        if character in {'"', "`"}:
            value, index = _read_quoted(expression, index + 1, character)
            tokens.append(_Token("quoted_identifier", value))
            continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(expression) and (
                expression[end].isalnum() or expression[end] in "_$"
            ):
                end += 1
            tokens.append(_Token("identifier", expression[index:end].lower()))
            index = end
            continue
        if character.isdigit():
            match = re.match(r"\d+(?:\.\d+)?", expression[index:])
            if match is None:
                raise _UnsupportedExpression
            value = match.group(0)
            tokens.append(_Token("number", value))
            index += len(value)
            continue
        two_character = expression[index : index + 2]
        if two_character in {"::", ">=", "<=", "!=", "<>"}:
            tokens.append(_Token("operator", two_character))
            index += 2
            continue
        if character in "=<>+-*/":
            tokens.append(_Token("operator", character))
            index += 1
            continue
        if character in "(),.[]":
            tokens.append(_Token("punctuation", character))
            index += 1
            continue
        raise _UnsupportedExpression
    return tuple(tokens)


def _read_quoted(expression: str, index: int, closing: str) -> tuple[str, int]:
    content: list[str] = []
    while index < len(expression):
        character = expression[index]
        if character == closing:
            if index + 1 < len(expression) and expression[index + 1] == closing:
                content.append(closing)
                index += 2
                continue
            return "".join(content), index + 1
        content.append(character)
        index += 1
    raise _UnsupportedExpression


def _canonicalize(
    expression: Expression,
    *,
    table_name: str,
    string_columns: frozenset[str],
) -> Expression:
    kind = expression[0]
    if kind == "identifier":
        parts = expression[1]
        if len(parts) > 1 and parts[-2] == table_name:
            parts = (parts[-1],)
        return ("identifier", parts)
    if kind == "cast":
        operand = _canonicalize(
            expression[1], table_name=table_name, string_columns=string_columns
        )
        type_spec = expression[2]
        source_dimensions = _string_dimensions(operand, string_columns)
        target_dimensions = _unbounded_string_dimensions(type_spec)
        if (
            target_dimensions is not None
            and source_dimensions == target_dimensions
        ):
            return operand
        return ("cast", operand, type_spec)

    canonical_items: list[object] = [kind]
    for item in expression[1:]:
        if isinstance(item, tuple) and item and isinstance(item[0], str):
            canonical_items.append(
                _canonicalize(item, table_name=table_name, string_columns=string_columns)
            )
        elif isinstance(item, tuple):
            canonical_items.append(
                tuple(
                    _canonicalize(value, table_name=table_name, string_columns=string_columns)
                    if isinstance(value, tuple) and value and isinstance(value[0], str)
                    else value
                    for value in item
                )
            )
        else:
            canonical_items.append(item)
    canonical = tuple(canonical_items)
    if kind == "compare" and canonical[1] == "=":
        left, right = canonical[2], canonical[3]
        if (
            _is_string_column(left, string_columns)
            and right[0] == "any"
            and right[1][0] == "array"
            and all(value[0] == "string" for value in right[1][1])
        ):
            return ("in", left, right[1][1])
    if kind == "in" and _is_string_column(canonical[1], string_columns):
        if all(value[0] == "string" for value in canonical[2]):
            return ("in", canonical[1], canonical[2])
    return canonical


def _is_string_column(expression: Expression, string_columns: frozenset[str]) -> bool:
    return (
        expression[0] == "identifier"
        and len(expression[1]) == 1
        and expression[1][0] in string_columns
    )


def _string_dimensions(
    expression: Expression,
    string_columns: frozenset[str],
) -> int | None:
    """Return the supported scalar/array dimension for a known string value."""

    if expression[0] == "string" or _is_string_column(expression, string_columns):
        return 0
    if expression[0] == "array":
        values = expression[1]
        if values and all(
            _string_dimensions(value, string_columns) == 0 for value in values
        ):
            return 1
        return None
    if expression[0] == "cast":
        return _supported_string_dimensions(expression[2])
    return None


def _supported_string_dimensions(type_spec: tuple[object, ...]) -> int | None:
    if type_spec[0] != "type" or type_spec[3] not in {0, 1}:
        return None
    if type_spec[1] not in {"text", "varchar", "character varying"}:
        return None
    return type_spec[3]


def _unbounded_string_dimensions(type_spec: tuple[object, ...]) -> int | None:
    if type_spec[0] != "type" or type_spec[2]:
        return None
    return _supported_string_dimensions(type_spec)


def canonical_check_expression(
    expression: str,
    *,
    table_name: str,
    string_columns: frozenset[str],
) -> Expression | None:
    """Return a comparable AST, or ``None`` when SQL is outside the safe grammar."""

    try:
        tokens = _tokenize(expression)
        parsed = _Parser(tokens).parse()
        return _canonicalize(
            parsed,
            table_name=table_name,
            string_columns=string_columns,
        )
    except _UnsupportedExpression:
        return None
