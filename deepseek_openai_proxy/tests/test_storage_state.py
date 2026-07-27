import json

from storage_state import (
    extract_bearer_token,
    extract_ds_session_id,
    inject_bearer_into_state,
    storage_state_login_issue,
    storage_state_summary,
)


def _sample_state(*, bearer: str | None = None, session_id: str = "abc123") -> dict:
    state = {
        "cookies": [
            {
                "name": "ds_session_id",
                "value": session_id,
                "domain": "chat.deepseek.com",
                "path": "/",
            }
        ],
        "origins": [
            {
                "origin": "https://chat.deepseek.com",
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


def test_storage_state_login_issue_requires_bearer():
    issue = storage_state_login_issue(_sample_state())
    assert issue is not None
    assert "Bearer" in issue


def test_storage_state_login_issue_passes_with_bearer():
    state = _sample_state(bearer="token-123")
    assert storage_state_login_issue(state) is None
    assert extract_bearer_token(state) == "Bearer token-123"


def test_inject_bearer_updates_user_token():
    state = inject_bearer_into_state(_sample_state(), "Bearer jwt-token")
    user_token = state["origins"][0]["localStorage"][0]["value"]
    assert json.loads(user_token)["value"] == "jwt-token"
    assert state["deepseek_auth"]["bearer_token"] == "jwt-token"


def test_extract_ds_session_id():
    assert extract_ds_session_id(_sample_state()) == "abc123"


def test_storage_state_summary():
    summary = storage_state_summary(_sample_state(bearer="abc"))
    assert "bearer=有" in summary
    assert "ds_session_id=有" in summary
