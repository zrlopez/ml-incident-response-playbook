def simple_threshold(current: float, baseline: float, pct: float = 0.2) -> bool:
    return current > baseline * (1 + pct)
