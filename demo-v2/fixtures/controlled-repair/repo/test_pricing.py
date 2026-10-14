from pricing import apply_discount


def test_quarter_off():
    assert apply_discount(200, 25) == 150


def test_no_discount():
    assert apply_discount(80, 0) == 80


def test_everything_off():
    assert apply_discount(10, 100) == 0
