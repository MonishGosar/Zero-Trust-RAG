import pytest

from scripts.answer_scoring import value_match


@pytest.mark.parametrize("expected,answer", [
    ("$54M", "$54 million [1]."), ("$54M", "$54,000,000 [1]."),
    ("$54M", "$0.054 billion [1]."), ("12%", "12 percent [1]."),
    ("1,420", "There were 1420 customers [1]."),
    ("enterprise contracts", "Enterprise\ncontracts drove growth [1]."),
])
def test_equivalent_values(expected, answer):
    assert value_match(expected, answer)


@pytest.mark.parametrize("expected,answer", [
    ("$54M", "$540 million"), ("$54M", "$54 billion"), ("$54M", "$-54 million"),
    ("$54M", "-$54 million"), ("12%", "112%"), ("1,420", "14200 customers"),
    ("renewals", "nonrenewals"),
])
def test_wrong_values_do_not_match(expected, answer):
    assert not value_match(expected, answer)
