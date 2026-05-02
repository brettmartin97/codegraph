def create_user(payload):
    """Create a user from payload and persist it."""
    validate_user(payload)
    return save_user(payload)


def validate_user(payload):
    if not payload.get("email"):
        raise ValueError("email required")


def save_user(payload):
    print("saving", payload)
    return {"id": "u1", **payload}
