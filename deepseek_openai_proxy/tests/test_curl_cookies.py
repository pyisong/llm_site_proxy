import pytest

from curl_cookies import cookie_header_to_playwright, extract_cookie_header, extract_user_agent, parse_curl_cookies


SAMPLE_CURL = """curl 'https://chat.deepseek.com/' \\
  -H 'accept: text/html' \\
  -b 'HWWAFSESTIME=1782456997014; HWWAFSESID=ebb9111e6c8eac72242; ds_session_id=bb904ad3aac74dfa96f6d344c90a6f4b' \\
  -H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
"""


def test_extract_cookie_header_from_curl():
    header = extract_cookie_header(SAMPLE_CURL)
    assert header is not None
    assert "ds_session_id=bb904ad3aac74dfa96f6d344c90a6f4b" in header


def test_extract_user_agent_from_curl():
    ua = extract_user_agent(SAMPLE_CURL)
    assert ua is not None
    assert "Chrome/149.0.0.0" in ua


def test_parse_curl_cookies():
    cookies, ua = parse_curl_cookies(SAMPLE_CURL)
    assert len(cookies) == 3
    assert cookies[0]["name"] == "HWWAFSESTIME"
    assert cookies[-1]["name"] == "ds_session_id"
    assert cookies[-1]["domain"] == ".deepseek.com"
    assert ua is not None


def test_cookie_header_url_decoding():
    cookies = cookie_header_to_playwright("foo=bar%3D%3D")
    assert cookies[0]["value"] == "bar=="


def test_missing_cookie_raises():
    with pytest.raises(ValueError, match="未找到"):
        parse_curl_cookies("curl 'https://example.com/'")
