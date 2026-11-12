import pytest


@pytest.fixture(autouse=True)
def _auth_required(monkeypatch):
    """The shipped default is no token (private network). The tests check the token path, so turn it on;
    test_auth_can_be_turned_off covers the default."""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
