from service import create_user


def route_create_user(payload):
    """HTTP handler for creating a user."""
    return create_user(payload)
