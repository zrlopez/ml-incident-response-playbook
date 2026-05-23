def required_fields_present(record: dict, required: list[str]) -> bool:
    return all(field in record for field in required)
