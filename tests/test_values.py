from decimal import Decimal

from roskazna_kbs.values import as_code, as_decimal, as_text, is_marker


def test_as_decimal_numbers_and_strings():
    assert as_decimal(14787403756198.48) == Decimal("14787403756198.48")
    assert as_decimal("37 283 772 222 778,74") == Decimal("37283772222778.74")
    assert as_decimal("37 283,5") == Decimal("37283.5")
    assert as_decimal(1480024.7100000018) == Decimal("1480024.7100000018")
    assert as_decimal(5) == Decimal(5)
    assert as_decimal("-1 234,00") == Decimal("-1234.00")


def test_as_decimal_empty_and_markers():
    assert as_decimal(None) is None
    assert as_decimal("") is None
    assert as_decimal("x") is None
    assert as_decimal("Х") is None
    assert as_decimal("-") is None
    assert as_decimal("в 1,7 раза") is None


def test_as_code_keeps_leading_zeros_and_letters():
    assert as_code(20.0, 3) == "020"
    assert as_code("10101011010000110") == "10101011010000110"
    assert as_code("012015111F") == "012015111F"
    assert as_code("12K") == "12K"
    assert as_code("100 01061002010001550") == "10001061002010001550"
    assert as_code(" х ") == "х"
    assert is_marker(as_code("X"))
    assert not is_marker(as_code("100"))


def test_as_text():
    assert as_text("  НАЛОГОВЫЕ\n И  НЕНАЛОГОВЫЕ ") == "НАЛОГОВЫЕ И НЕНАЛОГОВЫЕ"
    assert as_text(None) is None
    assert as_text(10.0) == "10"
