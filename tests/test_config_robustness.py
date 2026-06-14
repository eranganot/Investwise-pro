"""Config robustness: stray whitespace in env values must not crash boot."""
import pytest

from app.core.config import Settings


@pytest.mark.parametrize("val,expected", [("true ", True), (" true", True),
                                          ("True ", True), ("false ", False), ("1", True)])
def test_bool_env_tolerates_whitespace(val, expected):
    assert Settings(require_auth=val).require_auth is expected


def test_string_env_is_trimmed():
    assert Settings(allowed_emails=" a@x.com , b@y.com ").allowed_email_list == ["a@x.com", "b@y.com"]
