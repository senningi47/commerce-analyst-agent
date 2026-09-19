from commerce_agent.value_resolver._matching import (
    normalize_city,
    normalize_text,
    trigram_dice,
)


def test_normalization_is_deterministic() -> None:
    assert normalize_text("  CREDIT＿CARD  ") == "credit_card"
    assert normalize_text("PIX\t  Voucher") == "pix voucher"
    assert normalize_city(" São   Paulo ") == "sao paulo"


def test_trigram_dice_is_bounded_symmetric_and_exact_for_identity() -> None:
    score = trigram_dice("sao paulo", "sao paolo")

    assert 0.35 <= score < 1.0
    assert score == trigram_dice("sao paolo", "sao paulo")
    assert trigram_dice("sao paulo", "sao paulo") == 1.0
    assert trigram_dice("", "") == 1.0
