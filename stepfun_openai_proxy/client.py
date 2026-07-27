import sys

import httpx
from openai import OpenAI

BASE_URL = "http://localhost:18004"
API_KEY = "local-secret"


def preflight() -> None:
    try:
        response = httpx.get(
            f"{BASE_URL}/__debug/routes",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=5,
        )
    except httpx.HTTPError as exc:
        print(f"Cannot connect to {BASE_URL}: {exc}", file=sys.stderr)
        sys.exit(2)

    if response.status_code == 404:
        print(
            "Port 8000 is not running the current stepfun_openai_proxy app.\n"
            "Stop the old process, then restart with:\n\n"
            "  cd /Users/zhujianwei/projects/workspace/stepfun_openai_proxy\n"
            "  export STEPFUN_BACKEND=browser\n"
            "  export STEPFUN_PROXY_API_KEY=local-secret\n"
            "  export STEPFUN_BROWSER_PROFILE=/Users/zhujianwei/.stepfun-browser-profile\n"
            "  python3 -m uvicorn main:app --host 0.0.0.0 --port 8000\n",
            file=sys.stderr,
        )
        sys.exit(3)

    if response.status_code != 200:
        print(f"Route preflight failed: {response.status_code} {response.text}", file=sys.stderr)
        sys.exit(4)

    data = response.json()
    paths = {route["path"] for route in data.get("routes", [])}
    if "/v1/chat/completions" not in paths:
        print(f"Current service routes do not include /v1/chat/completions: {data}", file=sys.stderr)
        sys.exit(5)

    print(f"Preflight OK: backend={data.get('backend')} routes include /v1/chat/completions")


preflight()

client = OpenAI(api_key=API_KEY, base_url=f"{BASE_URL}/v1")

resp = client.chat.completions.create(
    model="stepfun-chat-web",
    messages=[
        {"role": "user", "content": "用一句话介绍一下你自己"}
    ],
)

print(resp.choices[0].message.content)
