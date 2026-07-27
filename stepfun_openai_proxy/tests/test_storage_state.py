import json

from storage_state import (
    extract_bearer_token,
    inject_bearer_into_state,
    extract_session_cookie,
    storage_state_login_issue,
    storage_state_summary,
)


def _sample_state(*, bearer: str | None = None, session_id: str = "abc123") -> dict:
    state = {
        "cookies": [
            {
                "name": "stepfun_session",
                "value": session_id,
                "domain": ".stepfun.com",
                "path": "/",
            }
        ],
        "origins": [
            {
                "origin": "https://chat.stepfun.com",
                "localStorage": [
                    {
                        "name": "userToken",
                        "value": json.dumps({"value": None, "__version": "0"}),
                    }
                ],
            }
        ],
    }
    if bearer:
        state = inject_bearer_into_state(state, bearer)
    return state


def test_storage_state_login_issue_accepts_cookie_or_local_storage():
    issue = storage_state_login_issue(_sample_state())
    assert issue is None


def test_storage_state_login_issue_rejects_empty_state():
    issue = storage_state_login_issue({"cookies": [], "origins": []})
    assert issue is not None
    assert "cookies" in issue


def test_inject_bearer_updates_user_token():
    state = inject_bearer_into_state(_sample_state(), "Bearer jwt-token")
    user_token = state["origins"][0]["localStorage"][0]["value"]
    assert json.loads(user_token)["value"] == "jwt-token"
    assert state["stepfun_auth"]["bearer_token"] == "jwt-token"


def test_extract_session_cookie():
    assert extract_session_cookie(_sample_state()) == "abc123"


def test_storage_state_summary():
    summary = storage_state_summary(_sample_state(bearer="abc"))
    assert "bearer=有" in summary
    assert "cookies=1" in summary
