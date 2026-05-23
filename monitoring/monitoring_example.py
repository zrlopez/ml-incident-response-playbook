from datetime import datetime


def emit_metric(name: str, value: float) -> dict:
    return {"metric": name, "value": value, "timestamp": datetime.utcnow().isoformat()}
