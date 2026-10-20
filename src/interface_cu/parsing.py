"""Explicit parsers. No model output, expressions, floating point or locale guessing."""

import re


PARSER_TYPES = {"text": "string", "usd_minor_units": "integer", "usd_currency": "string"}
MONEY = re.compile(r"USD (0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)\.([0-9]{2})")


def parse_value(parser: str, text: str) -> str | int:
    text = text.strip()
    if parser == "text":
        if not text:
            raise ValueError("EMPTY_TEXT")
        return text
    if parser not in {"usd_minor_units", "usd_currency"}:
        raise ValueError("UNSUPPORTED_PARSER")
    match = MONEY.fullmatch(text)
    if match is None:
        raise ValueError("INVALID_USD_AMOUNT")
    if parser == "usd_currency":
        return "USD"
    return int(match[1].replace(",", "")) * 100 + int(match[2])
