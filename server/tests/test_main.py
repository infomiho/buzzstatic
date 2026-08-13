import pytest

from server.main import access_control_warning


def test_warns_when_nobody_can_log_in():
    warning = access_control_warning(False, None, 0)
    assert warning is not None
    assert "Nobody can log in" in warning


@pytest.mark.parametrize(
    ("registration_open", "allowlist", "user_count"),
    [
        (True, None, 0),
        (False, frozenset({"alice"}), 0),
        (False, None, 2),
    ],
)
def test_no_warning_when_somebody_can_log_in(registration_open, allowlist, user_count):
    assert access_control_warning(registration_open, allowlist, user_count) is None
