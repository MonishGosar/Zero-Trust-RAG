"""Deterministic value matching for the synthetic answer smoke suite, not entailment."""

import re
import unicodedata
from decimal import Decimal

SCALES = {"": 1, "k": 1000, "thousand": 1000, "m": 1000000, "million": 1000000,
          "b": 1000000000, "billion": 1000000000}
NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MONEY = re.compile(r"(?<![\w-])\$\s*(" + NUMBER
                   + r")\s*(billion|million|thousand|[kmb])?(?!\w)", re.I)


def decimal(text):
    return Decimal(text.replace(",", ""))


def value_match(expected, answer):
    expected = " ".join(unicodedata.normalize("NFKC", expected).casefold().split())
    answer = " ".join(unicodedata.normalize("NFKC", answer).casefold().split())
    money = MONEY.fullmatch(expected)
    if money:
        amount = decimal(money[1]) * SCALES[money[2] or ""]
        return any(decimal(match[1]) * SCALES[match[2] or ""] == amount
                   for match in MONEY.finditer(answer))
    percent = re.fullmatch(r"(" + NUMBER + r")\s*%", expected)
    if percent:
        return any(decimal(match[1]) == decimal(percent[1]) for match in re.finditer(
            r"(?<![\w.-])(" + NUMBER + r")\s*(?:%|percent\b)", answer
        ))
    if re.fullmatch(NUMBER, expected):
        return any(decimal(match[0]) == decimal(expected) for match in re.finditer(
            r"(?<![\w.-])" + NUMBER + r"(?![\w.])", answer
        ))
    return bool(re.search(r"(?<!\w)" + re.escape(expected) + r"(?!\w)", answer))
