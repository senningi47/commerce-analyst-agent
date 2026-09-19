"""Pure deterministic normalization and bounded similarity helpers."""

import re
import unicodedata


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_city(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_text(value))
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _trigrams(value: str) -> set[str]:
    padded = f"  {value}  "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def trigram_dice(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    left_trigrams = _trigrams(left)
    right_trigrams = _trigrams(right)
    return 2 * len(left_trigrams & right_trigrams) / (len(left_trigrams) + len(right_trigrams))
