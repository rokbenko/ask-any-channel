"""Chat completion cost estimation. Estimate-only, never billing-accurate — see
core/constants.py::CHAT_MODEL_PRICING_USD. Must be labelled 'estimated' wherever the UI
prints it, per CLAUDE.md."""

from core.constants import CHAT_MODEL_PRICING_USD


def estimate_cost_usd(model: str, tokens_in: int | None, tokens_out: int | None) -> float | None:
    pricing = CHAT_MODEL_PRICING_USD.get(model)
    if pricing is None or tokens_in is None or tokens_out is None:
        return None
    return (tokens_in / 1000) * pricing.input_per_1k_usd + (
        tokens_out / 1000
    ) * pricing.output_per_1k_usd
