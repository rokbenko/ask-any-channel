from core.chat.pricing import estimate_cost_usd


def test_known_model_computes_cost_from_tokens():
    cost = estimate_cost_usd("gpt-4.1-mini", tokens_in=1000, tokens_out=1000)
    assert cost == 0.0004 + 0.0016


def test_unknown_model_returns_none():
    assert estimate_cost_usd("some-unlisted-model", tokens_in=100, tokens_out=100) is None


def test_missing_token_counts_returns_none():
    assert estimate_cost_usd("gpt-4.1-mini", tokens_in=None, tokens_out=100) is None
    assert estimate_cost_usd("gpt-4.1-mini", tokens_in=100, tokens_out=None) is None
