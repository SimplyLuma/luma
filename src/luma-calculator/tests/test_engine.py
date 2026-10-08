"""Calculator release arithmetic: assertions that exercise the real parser and key state."""

import sys
from pathlib import Path
import unittest
from decimal import Decimal
import math

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luma_calc import Calculator, CalculationError, evaluate, format_decimal


class ParserTests(unittest.TestCase):
    def assert_decimal(self, expression: str, expected: str) -> None:
        self.assertEqual(evaluate(expression), Decimal(expected), expression)

    def test_precedence_and_parentheses(self):
        for expression, expected in (
            ("2+3×4", "14"), ("(2+3)×4", "20"), ("8÷2×3", "12"),
            ("2^3^2", "512"), ("−2^2", "-4"), ("(−2)^2", "4"),
            ("3(4+5)", "27"), ("2π÷π", "2"), ("5mod2", "1"),
            ("−5mod3", "1"),
        ):
            with self.subTest(expression=expression):
                self.assert_decimal(expression, expected)

    def test_exact_decimal_and_percent(self):
        for expression, expected in (
            ("0.1+0.2", "0.3"), ("1.25×0.08", "0.1000"),
            ("200+10%", "220"), ("200−10%", "180"),
            ("200×10%", "20"), ("50%", "0.5"), ("3!", "6"),
        ):
            with self.subTest(expression=expression):
                self.assert_decimal(expression, expected)

    def test_huge_and_tiny(self):
        self.assert_decimal("999999999999999999999999+1", "1000000000000000000000000")
        self.assert_decimal("0.000000000000000000001×3", "0.000000000000000000003")
        self.assert_decimal("1E−40+2E−40", "3E-40")
        self.assertTrue(format_decimal(Decimal("1E+30")).startswith("1E"))
        self.assertTrue(format_decimal(Decimal("1E-30")).startswith("1E"))

    def test_long_integers_do_not_silently_round(self):
        large = "9" * 100
        self.assertEqual(evaluate(f"{large}+2"), Decimal(int(large) + 2))
        self.assertEqual(evaluate(f"{large}×{large}"), Decimal(int(large) ** 2))
        self.assertEqual(evaluate("2^1000"), Decimal(2 ** 1000))
        self.assertEqual(evaluate("2^1000+1"), Decimal(2 ** 1000 + 1))
        self.assertEqual(evaluate("1E100+1"), Decimal(10 ** 100 + 1))
        self.assertEqual(evaluate("1E-100+1"), Decimal("1." + "0" * 99 + "1"))
        self.assertEqual(evaluate("1000!"), Decimal(math.factorial(1000)))

    def test_scientific(self):
        self.assert_decimal("sqrt(144)", "12")
        self.assert_decimal("abs(−12)", "12")
        self.assert_decimal("2³+3²", "17")
        self.assert_decimal("2+4⁻¹", "2.25")
        self.assert_decimal("(2+2)⁻¹", "0.25")
        self.assert_decimal("2E3", "2000")
        self.assert_decimal("sin(30)", "0.5")

    def test_invalid(self):
        for expression in ("1÷0", "0÷0", "sqrt(−1)", "(1+2", "2+", "hello(1)", "1;2", "(−3)!"):
            with self.subTest(expression=expression):
                with self.assertRaises(CalculationError):
                    evaluate(expression)


class KeyStateTests(unittest.TestCase):
    def press(self, calculator: Calculator, sequence: str) -> None:
        for key in sequence.split():
            calculator.press(key)

    def test_repeated_equals_and_tape(self):
        calculator = Calculator()
        self.press(calculator, "2 + 3 =")
        self.assertEqual(calculator.display, "5")
        self.assertEqual(calculator.tape[0][0], "2+3")
        calculator.press("=")
        self.assertEqual(calculator.display, "8")
        calculator.press("=")
        self.assertEqual(calculator.display, "11")
        calculator.use_tape(2)
        self.assertEqual(calculator.display, "5")

    def test_percent_repeated_equals(self):
        calculator = Calculator()
        self.press(calculator, "2 0 0 + 1 0 % = =")
        self.assertEqual(calculator.display, "242.00")

    def test_clear_backspace_and_new_number(self):
        calculator = Calculator()
        self.press(calculator, "1 2 back 3 =")
        self.assertEqual(calculator.display, "13")
        calculator.press("4")
        self.assertEqual(calculator.display, "4")
        calculator.press("ac")
        self.assertEqual(calculator.display, "0")
        self.assertTrue(calculator.tape)
        calculator.press("ac")
        self.assertFalse(calculator.tape)

    def test_sign_and_divide_by_zero(self):
        calculator = Calculator()
        self.press(calculator, "5 neg")
        self.assertEqual(evaluate(calculator.display), Decimal(-5))
        calculator.press("neg")
        self.assertEqual(evaluate(calculator.display), Decimal(5))
        self.press(calculator, "÷ 0 =")
        self.assertEqual(calculator.error, "Can’t divide by 0")
        self.assertEqual(calculator.tape, [])

    def test_sign_after_binary_minus_and_autoclose(self):
        calculator = Calculator()
        self.press(calculator, "5 − 3 neg =")
        self.assertEqual(calculator.display, "8")
        calculator.press("ac")
        self.press(calculator, "( 2 + 3 =")
        self.assertEqual(calculator.display, "5")
        calculator.press("ac")
        self.press(calculator, "3 × neg 2")
        self.assertEqual(calculator.display, "3×−2")
        calculator.press("=")
        self.assertEqual(calculator.display, "-6")

    def test_toggles(self):
        calculator = Calculator()
        calculator.press("deg")
        calculator.press("2nd")
        self.assertFalse(calculator.degrees)
        self.assertTrue(calculator.second)

    def test_reciprocal_operates_on_current_value(self):
        calculator = Calculator()
        self.press(calculator, "2 + 4 ⁻¹ =")
        self.assertEqual(calculator.display, "2.25")
        calculator.press("π")
        self.assertEqual(calculator.display, "π")

    def test_tape_result_reuse_in_an_expression(self):
        calculator = Calculator(tape=[("2+3", Decimal(5))])
        self.press(calculator, "4")
        calculator.use_tape(0)
        self.assertEqual(calculator.display, "4×5")
        self.press(calculator, "+")
        calculator.use_tape(0)
        self.assertEqual(calculator.display, "4×5+5")

    def test_implicit_value_keys_and_unmatched_close(self):
        calculator = Calculator()
        self.press(calculator, "2 π")
        self.assertEqual(calculator.display, "2×π")
        calculator.press(")")
        self.assertEqual(calculator.display, "2×π")
        calculator.press("sin")
        self.assertEqual(calculator.display, "2×π×sin(")

    def test_trigonometry_is_decimal_not_float(self):
        for expression, expected in (
            ("sin(30)", "0.5"), ("cos(60)", "0.5"), ("tan(45)", "1"), ("sin(90)", "1"),
            ("cos(90)", "0"), ("sin(180)", "0"), ("cos(180)", "-1"), ("sin(−30)", "-0.5"),
            ("sin(390)", "0.5"), ("asin(0.5)", "30"), ("acos(0.5)", "60"), ("atan(1)", "45"),
            ("acos(−1)", "180"), ("sin(1000000)", "-0.98480775301220805936674302458952301367064325172"),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression), Decimal(expected), expression)
        self.assertEqual(evaluate("sin(π)", degrees=False), Decimal(0))
        self.assertEqual(evaluate("cos(π÷2)", degrees=False), Decimal(0))
        self.assertEqual(format_decimal(evaluate("sin(1)", degrees=False))[:18], "0.8414709848078965")
        for expression in ("tan(90)", "asin(2)", "acos(−2)"):
            with self.subTest(expression=expression), self.assertRaises(CalculationError):
                evaluate(expression)

    def test_rounding_noise_is_settled_but_exact_results_are_kept(self):
        for expression, expected in (("1÷3×3", "1"), ("1÷7×7", "1"), ("sqrt(2)^2", "2"),
                                     ("cbrt(27)", "3"), ("27root3", "3"), ("ln(e)", "1"),
                                     ("0.1+0.2", "0.3"), ("1.25×0.08", "0.1000")):
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression), Decimal(expected), expression)
        exact = "12345678901234567890123456789012345678901234567890×98765432109876543210"
        self.assertEqual(evaluate(exact), Decimal(12345678901234567890123456789012345678901234567890
                                                  * 98765432109876543210))
        self.assertEqual(format_decimal(evaluate("2÷3")), "0." + "6" * 47 + "7")

    def test_backspace_removes_a_whole_function_and_one_digit(self):
        calculator = Calculator()
        self.press(calculator, "2 + 4 5")
        calculator.press("back")
        self.assertEqual(calculator.display, "2+4")
        calculator.press("sin")
        self.assertEqual(calculator.display, "2+4×sin(")
        calculator.press("back")
        self.assertEqual(calculator.display, "2+4×")
        calculator.press("back")
        calculator.press("back")
        calculator.press("back")
        calculator.press("back")
        self.assertEqual(calculator.display, "0")
        calculator.press("back")
        self.assertEqual(calculator.display, "0")
        self.press(calculator, "5 ⁻¹")
        calculator.press("back")
        self.assertEqual(calculator.display, "5")
        self.press(calculator, "mod")
        calculator.press("back")
        self.assertEqual(calculator.display, "5")

    def test_backspace_after_an_answer_keeps_exact_digits(self):
        calculator = Calculator()
        self.press(calculator, "0 . 1 + 0 . 2 =")
        self.assertEqual(calculator.display, "0.3")
        calculator.press("back")
        self.assertEqual(calculator.display, "0.")
        self.assertFalse(calculator.fresh)

    def test_fixture_is_memory_only(self):
        fixture = Path(__file__).resolve().parents[3] / "tests/fixtures/calc-v71.json"
        original = fixture.read_bytes()
        calculator = Calculator.from_fixture(fixture)
        self.press(calculator, "4 × 5 =")
        self.assertEqual(calculator.display, "20")
        self.assertEqual(fixture.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
