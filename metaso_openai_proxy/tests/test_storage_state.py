from storage_state import (
    extract_cookie_header,
    extract_uid_sid,
    storage_state_login_issue,
)


def _state(**kwargs):
    cookies = kwargs.get("cookies", [])
    return {"cookies": cookies, "origins": []}


def test_extract_uid_sid_and_header():
    state = _state(
        cookies=[
            {"name": "uid", "value": "u1", "domain": ".metaso.cn"},
            {"name": "sid", "value": "s1", "domain": ".metaso.cn"},
            {"name": "other", "value": "x", "domain": ".metaso.cn"},
            {"name": "JSESSIONID", "value": "files", "domain": "files.metaso.cn"},
            {"name": "JSESSIONID", "value": "main", "domain": "metaso.cn"},
            {"name": "empty", "value": "", "domain": ".metaso.cn"},
        ]
    )
    assert extract_uid_sid(state) == ("u1", "s1")
    header = extract_cookie_header(state)
    assert "uid=u1" in header and "sid=s1" in header and "other=x" in header
    assert "JSESSIONID=main" in header
    assert "files" not in header
    assert "empty=" not in header
    assert storage_state_login_issue(state) is None


def test_login_issue_when_missing_sid():
    state = _state(cookies=[{"name": "uid", "value": "u1"}])
    assert extract_uid_sid(state) == ("u1", None)
    assert storage_state_login_issue(state)
