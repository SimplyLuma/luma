"""Decimal calculator grammar and key state. No Python evaluation or GTK here.

The input grammar has ordinary arithmetic precedence, right associative powers,
postfix percent, parentheses, constants, and scientific functions. Percent in
an addition or subtraction is relative to the value on its left, as on v70.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow, getcontext, localcontext
import json
import math
from pathlib import Path
import random
import re


PRECISION = 60
SETTLE_GUARD = 12
MAX_EXACT_DIGITS = 100000
PI = Decimal("3.141592653589793238462643383279502884197169399375105820974944")
E = Decimal("2.718281828459045235360287471352662497757247093699959574966967")
_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
_FUNCTIONS = {"sin", "cos", "tan", "asin", "acos", "atan", "ln", "log", "log2", "sqrt", "cbrt", "exp", "p10", "abs"}
_BINARY = {"+", "−", "×", "÷", "mod", "^", "root", "E"}
# What ⌫ removes as one piece: a function with its bracket ("sin("), a word operator, a power of −1.
_BACK_TOKEN = re.compile(r"(?:(?:%s)\(|mod|root|⁻¹)$" % "|".join(sorted(_FUNCTIONS, key=len, reverse=True)))


class CalculationError(ValueError):
    """A complete expression that has no real, finite calculator result."""


@dataclass(frozen=True)
class _Value:
    number: Decimal
    percent: bool = False


def _tokens(expression: str) -> list[str]:
    result: list[str] = []
    at = 0
    while at < len(expression):
        char = expression[at]
        if char.isspace() or char == ",":
            at += 1
            continue
        if expression.startswith("⁻¹", at):
            result.append("⁻¹")
            at += 2
            continue
        match = _NUMBER.match(expression, at)
        if match:
            result.append(match.group())
            at = match.end()
            continue
        match = re.match(r"[A-Za-z]+", expression[at:])
        if match:
            word = match.group()
            if word not in _FUNCTIONS | {"mod", "root", "E", "e"}:
                raise CalculationError(f"Unknown function: {word}")
            result.append(word)
            at += len(word)
            continue
        if char in "+−-×*÷/^()%!²³πe":
            result.append({"-": "−", "*": "×", "/": "÷"}.get(char, char))
            at += 1
            continue
        raise CalculationError(f"Unexpected character: {char}")
    return result


def _settle(value: Decimal, precision: int) -> Decimal:
    """Drop the guard digits of an answer that division or a root had to round.

    1÷3×3 carries sixty 9s at working precision; the answer is 1. Only an answer the context
    marked Inexact comes here, so a result that is exactly representable is never touched.
    """
    with localcontext() as context:
        context.prec = max(1, precision - SETTLE_GUARD)
        rounded = +value
    whole = rounded.to_integral_value()
    return whole if rounded == whole else rounded.normalize()


class _Parser:
    def __init__(self, expression: str, *, degrees: bool = True) -> None:
        self.tokens = _tokens(expression)
        self.index = 0
        self.degrees = degrees
        self.repeat: tuple[str, Decimal, bool] | None = None

    def peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> str:
        item = self.peek()
        if item is None:
            raise CalculationError("Incomplete expression")
        self.index += 1
        return item

    def parse(self) -> Decimal:
        if not self.tokens:
            return Decimal(0)
        with localcontext() as context:
            # Decimal's default precision would silently round long integer
            # literals before they reach the display. Reserve enough places
            # for all literal magnitudes and fractional scales in this input.
            required = 8
            for token in self.tokens:
                if _NUMBER.fullmatch(token):
                    number = Decimal(token)
                    exponent = number.as_tuple().exponent
                    required += max(len(number.as_tuple().digits),
                                    max(0, number.adjusted() + 1),
                                    max(0, -exponent))
            if required > MAX_EXACT_DIGITS:
                raise CalculationError("Result is too large")
            context.prec = max(PRECISION, required)
            working = context.prec
            context.Emax = 999999
            context.Emin = -999999
            context.clear_flags()
            try:
                result = self._sum().number
                if self.peek() is not None:
                    raise CalculationError("Incomplete expression")
                if not result.is_finite():
                    raise CalculationError("Result is too large")
                result = +result
                if context.flags[Inexact]:
                    result = _settle(result, working)
                return result
            except (DivisionByZero, ZeroDivisionError):
                raise CalculationError("Can’t divide by 0") from None
            except (InvalidOperation, Overflow, OverflowError) as error:
                raise CalculationError("Result is undefined") from error

    def _sum(self) -> _Value:
        left = self._product()
        while self.peek() in ("+", "−"):
            operation = self.take()
            right = self._product()
            operand = left.number * right.number if right.percent else right.number
            self.repeat = (operation, right.number, right.percent)
            left = _Value(left.number + operand if operation == "+" else left.number - operand)
        return left

    def _product(self) -> _Value:
        left = self._signed()
        while True:
            operation = self.peek()
            implicit = self._starts_value(operation)
            if operation not in ("×", "÷", "mod") and not implicit:
                return left
            if not implicit:
                self.take()
            else:
                operation = "×"
            right = self._signed()
            self.repeat = (operation, right.number, False)
            if operation == "×":
                left = _Value(left.number * right.number)
            elif operation == "÷":
                if right.number == 0:
                    raise CalculationError("Can’t divide by 0")
                left = _Value(left.number / right.number)
            else:
                if right.number == 0:
                    raise CalculationError("Can’t divide by 0")
                left = _Value(((left.number % right.number) + right.number) % right.number)

    @staticmethod
    def _starts_value(item: str | None) -> bool:
        return bool(item and (item == "(" or item in ("π", "e", "√") or item in _FUNCTIONS or item[0].isdigit() or item[0] == "."))

    def _signed(self) -> _Value:
        if self.peek() == "+":
            self.take()
            return self._signed()
        if self.peek() == "−":
            self.take()
            value = self._signed()
            return _Value(-value.number, value.percent)
        return self._power()

    def _power(self) -> _Value:
        left = self._postfix()
        if self.peek() in ("^", "root", "E"):
            operation = self.take()
            right = self._signed()
            self.repeat = (operation, right.number, False)
            if operation == "^":
                if (right.number == right.number.to_integral_value()
                        and left.number not in (Decimal(0), Decimal(1), Decimal(-1))):
                    power = abs(int(right.number))
                    needed = len(left.number.as_tuple().digits) * power + 8
                    if needed > MAX_EXACT_DIGITS:
                        raise CalculationError("Result is too large")
                    getcontext().prec = max(getcontext().prec, needed)
                return _Value(left.number.__pow__(right.number))
            if operation == "E":
                return _Value(left.number * Decimal(10).__pow__(right.number))
            if right.number == 0:
                raise CalculationError("A root of 0 is undefined")
            if left.number < 0 and right.number == right.number.to_integral_value() and int(right.number) % 2:
                return _Value(-((-left.number) ** (Decimal(1) / right.number)))
            if left.number < 0:
                raise CalculationError("No real root")
            return _Value(left.number ** (Decimal(1) / right.number))
        return left

    def _postfix(self) -> _Value:
        value = self._primary()
        while self.peek() in ("%", "!", "²", "³", "⁻¹"):
            operation = self.take()
            if operation == "%":
                value = _Value(value.number / 100, True)
            elif operation == "²":
                value = _Value(value.number ** 2)
            elif operation == "³":
                value = _Value(value.number ** 3)
            elif operation == "⁻¹":
                if value.number == 0:
                    raise CalculationError("Can’t divide by 0")
                value = _Value(Decimal(1) / value.number)
            else:
                number = value.number
                if number < 0 or number != number.to_integral_value():
                    raise CalculationError("Factorial needs a whole number")
                if number > 10000:
                    raise CalculationError("Factorial is too large")
                exact = Decimal(math.factorial(int(number)))
                digits = len(exact.as_tuple().digits)
                if digits > MAX_EXACT_DIGITS:
                    raise CalculationError("Factorial is too large")
                getcontext().prec = max(getcontext().prec, digits)
                value = _Value(exact)
        return value

    def _primary(self) -> _Value:
        item = self.take()
        if item == "(":
            value = self._sum()
            if self.take() != ")":
                raise CalculationError("Incomplete expression")
            return value
        if item == "π":
            return _Value(PI)
        if item == "e":
            return _Value(E)
        if item == "√":
            return _Value(self._function("sqrt", self._signed().number))
        if item in _FUNCTIONS:
            if self.peek() == "(":
                self.take()
                argument = self._sum().number
                if self.take() != ")":
                    raise CalculationError("Incomplete expression")
            else:
                argument = self._signed().number
            return _Value(self._function(item, argument))
        if _NUMBER.fullmatch(item):
            return _Value(Decimal(item))
        raise CalculationError("Incomplete expression")

    def _function(self, name: str, value: Decimal) -> Decimal:
        if name == "abs":
            return abs(value)
        if name == "sqrt":
            if value < 0:
                raise CalculationError("No real square root")
            return value.sqrt()
        if name == "cbrt":
            return (Decimal(-1) if value < 0 else Decimal(1)) * abs(value) ** (Decimal(1) / 3)
        if name == "exp":
            return value.exp()
        if name == "p10":
            return Decimal(10) ** value
        if name in ("ln", "log", "log2"):
            if value <= 0:
                raise CalculationError("Log needs a number above 0")
            return value.ln() if name == "ln" else value.log10() if name == "log" else value.ln() / Decimal(2).ln()
        return _trig(name, value, self.degrees)


# Trigonometry in Decimal, so sin(30°) is 0.5 and not 0.49999999999999994. The series run with ten guard
# digits; `_Parser.parse` settles the answer back to the working precision.
_GUARD = 10
_ZERO_BELOW = Decimal(10) ** -(PRECISION - 6)


def _sin_cos(x: Decimal) -> tuple[Decimal, Decimal]:
    """sin and cos of x, |x| at most π, by their Taylor series."""
    square = x * x
    sine, cosine = x, Decimal(1)
    sine_term, cosine_term = x, Decimal(1)
    limit = Decimal(10) ** -(getcontext().prec + 2)
    n = 1
    while abs(sine_term) > limit or abs(cosine_term) > limit:
        cosine_term = -cosine_term * square / ((2 * n - 1) * (2 * n))
        sine_term = -sine_term * square / ((2 * n) * (2 * n + 1))
        cosine += cosine_term
        sine += sine_term
        n += 1
    return sine, cosine


def _reduce(value: Decimal, degrees: bool) -> tuple[Decimal, int | None]:
    """The angle as radians within ±π, plus its quarter-turn count when it is exactly one (else None)."""
    full = Decimal(360) if degrees else 2 * PI
    try:
        turn = value % full
    except InvalidOperation:
        raise CalculationError("Angle is too large") from None
    if turn < 0:
        turn += full
    quarter = None
    if degrees:
        if turn % 90 == 0:
            quarter = int(turn // 90)
        turn = turn * PI / 180
    if turn > PI:
        turn -= 2 * PI
    return turn, quarter


def _trig(name: str, value: Decimal, degrees: bool) -> Decimal:
    outer = getcontext()
    with localcontext() as context:
        context.prec = outer.prec + _GUARD
        if name in ("sin", "cos", "tan"):
            angle, quarter = _reduce(value, degrees)
            if quarter is not None:
                sine, cosine = (Decimal(x) for x in ((0, 1), (1, 0), (0, -1), (-1, 0))[quarter])
            else:
                sine, cosine = _sin_cos(angle)
                if abs(value) >= Decimal("0.001"):   # a turn was taken off: what is left of 0 is rounding
                    sine = Decimal(0) if abs(sine) < _ZERO_BELOW else sine
                    cosine = Decimal(0) if abs(cosine) < _ZERO_BELOW else cosine
            if name == "tan":
                if cosine == 0:
                    raise CalculationError("Tangent is undefined here")
                answer = sine / cosine
            else:
                answer = sine if name == "sin" else cosine
        else:
            if name in ("asin", "acos") and abs(value) > 1:
                raise CalculationError("Result is undefined")
            answer = _inverse(name, value)
            if degrees:
                answer = answer * 180 / PI
    outer.flags[Inexact] = True
    return +answer


def _inverse(name: str, value: Decimal) -> Decimal:
    """asin, acos or atan in radians: a float guess refined by Newton's method in Decimal."""
    if name == "acos":
        return PI / 2 - _inverse("asin", value)
    if name == "asin" and abs(value) == 1:
        return PI / 2 * (1 if value > 0 else -1)
    guess = Decimal(repr((math.asin if name == "asin" else math.atan)(float(value))))
    for _ in range(5):
        sine, cosine = _sin_cos(guess)
        if name == "asin":
            guess -= (sine - value) / cosine
        else:
            guess -= cosine * (sine - value * cosine)
    return guess


def evaluate(expression: str, *, degrees: bool = True) -> Decimal:
    """Evaluate a calculator expression with exact decimal basic arithmetic."""
    return _Parser(expression, degrees=degrees).parse()


def format_decimal(value: Decimal) -> str:
    """Readable tabular result, retaining significant digits without float conversion."""
    if not value.is_finite():
        raise CalculationError("Result is undefined")
    if value == 0:
        return "0"
    if value.adjusted() >= 15 or value.adjusted() < -9:
        with localcontext() as context:
            context.prec = 12
            return format(+value, "E").replace("E+", "E")
    plain = format(value, "f")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    integer, dot, fractional = plain.partition(".")
    sign = "−" if integer.startswith("-") else ""
    integer = integer.lstrip("-")
    grouped = f"{int(integer):,}"
    return sign + grouped + (dot + fractional if dot else "")


@dataclass
class Calculator:
    """One in-memory calculation session. No user store is read or written."""

    expression: str = ""
    tape: list[tuple[str, Decimal]] = field(default_factory=list)
    scientific: bool = False
    second: bool = False
    degrees: bool = True
    fresh: bool = False
    error: str = ""
    _repeat: tuple[str, Decimal, bool] | None = None

    def _ends_value(self) -> bool:
        return bool(re.search(r"(?:\d|[πe)!%²³]|⁻¹)$", self.expression))

    @classmethod
    def from_fixture(cls, path: str | Path) -> "Calculator":
        """Load the v70 sample state into memory; this never opens a real store."""
        source = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {"expression", "tape", "scientific", "second", "degrees"}
        if not isinstance(source, dict) or set(source) != allowed:
            raise ValueError("Invalid Calculator fixture")
        if not isinstance(source["tape"], list):
            raise ValueError("Invalid Calculator tape fixture")
        tape = [(str(item["expression"]), Decimal(str(item["result"]))) for item in source["tape"]]
        return cls(expression=str(source["expression"]), tape=tape,
                   scientific=bool(source["scientific"]), second=bool(source["second"]),
                   degrees=bool(source["degrees"]))

    @property
    def display(self) -> str:
        return self.expression or "0"

    @property
    def preview(self) -> str:
        if self.error:
            return self.error
        if self.fresh or not self.expression or _NUMBER.fullmatch(self.expression):
            return ""
        try:
            return "= " + format_decimal(evaluate(self.expression, degrees=self.degrees))
        except CalculationError:
            return ""

    def press(self, key: str) -> None:
        self.error = ""
        if key in "0123456789" or key == ".":
            if self.fresh:
                self.expression = ""
                self.fresh = False
                self._repeat = None
            if key == "." and re.search(r"\d+\.\d*$", self.expression):
                return
            if key != "." and self.expression in ("0", "−0"):
                self.expression = ("−" if self.expression.startswith("−") else "") + key
            elif key != "." and self.expression.endswith("−0") and (
                    len(self.expression) == 2 or self.expression[-3] in "+−×÷^("):
                self.expression = self.expression[:-1] + key
            else:
                self.expression += "0." if key == "." and (not self.expression or not self.expression[-1].isdigit()) else key
        elif key in _BINARY:
            self.fresh = False
            if not self.expression:
                self.expression = "−" if key == "−" else (str(self.tape[0][1]) if self.tape else "0") + key
            elif self.expression[-1] in "+−×÷^":
                if key == "−" and self.expression[-1] != "−":
                    self.expression += "−"
                else:
                    self.expression = self.expression[:-1] + key
            else:
                self.expression += key
        elif key in ("(", ")", "π", "e"):
            if self.fresh:
                self.expression = ""
            self.fresh = False
            if key == ")":
                if self.expression.count("(") > self.expression.count(")") and self._ends_value():
                    self.expression += ")"
                return
            if self._ends_value():
                self.expression += "×"
            self.expression += key
        elif key in ("%", "!", "²", "³", "⁻¹"):
            if self.expression:
                self.expression += key
                self.fresh = False
        elif key in _FUNCTIONS:
            if self.fresh and self.expression:
                self.expression = f"{key}({self.expression})"
            else:
                self.expression += ("×" if self._ends_value() else "") + f"{key}("
            self.fresh = False
        elif key == "rand":
            if self.fresh:
                self.expression = ""
            if self._ends_value():
                self.expression += "×"
            self.expression += str(Decimal(random.randrange(0, 1_000_000)) / 1_000_000)
            self.fresh = False
        elif key == "neg":
            match = re.search(r"\d+(?:\.\d*)?$", self.expression)
            if match:
                prefix, number = self.expression[:match.start()], match.group()
                unary = prefix.endswith("−") and (len(prefix) == 1 or prefix[-2] in "+−×÷^(E")
                self.expression = (prefix[:-1] if unary else prefix + "−") + number
            elif not self.expression or self.expression[-1] in "+−×÷^(":
                self.expression += "−0"
            elif self.expression:
                self.expression = f"−({self.expression})"
            self.fresh = False
        elif key == "back":
            token = _BACK_TOKEN.search(self.expression)
            self.expression = self.expression[:token.start()] if token else self.expression[:-1]
            self.fresh = False
        elif key == "ac":
            if self.expression:
                self.expression = ""
                self.fresh = False
                self._repeat = None
            else:
                self.tape.clear()
        elif key == "=":
            self.equals()
        elif key == "2nd":
            self.second = not self.second
        elif key == "deg":
            self.degrees = not self.degrees
        else:
            raise ValueError(f"Unknown key: {key}")

    def equals(self) -> None:
        if not self.expression:
            return
        if self.fresh:
            if self._repeat is None:
                return
            operation, operand, percent = self._repeat
            base = Decimal(self.expression)
            right = base * operand if percent else operand
            with localcontext() as context:
                context.prec = PRECISION
                if operation == "+":
                    answer = base + right
                elif operation == "−":
                    answer = base - right
                elif operation == "×":
                    answer = base * right
                elif operation == "÷":
                    if right == 0:
                        self.error = "Can’t divide by 0"
                        return
                    answer = base / right
                else:
                    return
            original = f"{self.expression}{operation}{format_decimal(operand)}{'%' if percent else ''}"
        else:
            original = self.expression
            missing = original.count("(") - original.count(")")
            parser = _Parser(original + ")" * max(0, missing), degrees=self.degrees)
            try:
                answer = parser.parse()
            except CalculationError as error:
                self.error = str(error)
                return
            self._repeat = parser.repeat
        self.tape.insert(0, (original, answer))
        self.expression = str(answer)
        self.fresh = True

    def use_tape(self, index: int) -> None:
        answer = str(self.tape[index][1])
        if self.fresh or not self.expression:
            self.expression = answer
        else:
            if self._ends_value():
                self.expression += "×"
            self.expression += answer
        self.fresh = False
        self.error = ""
