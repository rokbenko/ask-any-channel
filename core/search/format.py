"""Small rendering helpers shared by search-result display surfaces (aac search, aac retrieval
compare) — kept out of the CLI modules themselves so they stay logic-free."""


def snippet(text: str, max_len: int = 200) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


def mmss(t_start_s: float) -> str:
    total = int(t_start_s)
    return f"{total // 60}:{total % 60:02d}"
