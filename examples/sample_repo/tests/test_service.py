from service import create_user


def test_create_user():
    assert create_user({"email": "a@example.com"})["id"]
