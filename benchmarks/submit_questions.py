import requests
import json
import os
from pathlib import Path

BASE_URL = "http://localhost:8000"

EMAIL = os.environ.get("LIZARD_EMAIL", "dummyuser@gmail.com")
PASSWORD = os.environ.get("LIZARD_PASSWORD", "@DummyUserPA22")
TOKEN_CACHE_FILE = Path(".lizard_auth_token.json")

def _load_cached_token():
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        token = payload.get("access_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    except Exception:
        pass
    return None


def _save_cached_token(token: str) -> None:
    try:
        TOKEN_CACHE_FILE.write_text(
            json.dumps({"access_token": token}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Warning: failed to persist auth token cache: {exc}")


def authenticate(force_refresh: bool = False):
    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    response = requests.post(
        f"{BASE_URL}/auth/login",
        headers={"Content-Type": "application/json"},
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    if response.status_code == 200:
        token = response.json().get("access_token")
        if not token:
            print("Authentication failed: no access token returned")
            exit(1)
        _save_cached_token(token)
        return token

    print("Authentication failed:", response.text)
    exit(1)


def authenticated_request(method: str, path: str, auth_state: dict, **kwargs) -> requests.Response:
    token = auth_state.get("token")
    if not token:
        auth_state["token"] = authenticate(force_refresh=False)

    headers = dict(kwargs.pop("headers", {}) or {})
    for attempt in range(2):
        headers["Authorization"] = f"Bearer {auth_state['token']}"
        response = requests.request(
            method=method,
            url=f"{BASE_URL}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )

        if response.status_code not in (401, 403):
            return response

        if attempt == 0:
            print("Auth token expired/invalid. Refreshing login...")
            auth_state["token"] = authenticate(force_refresh=True)
            continue

        return response

    return response

def load_questions(qtype="general"):
    from questions import questions
    questions = questions[qtype]
    return questions

def submit_question(auth_state, question):
    response = authenticated_request(
        method="POST",
        path="/runs",
        auth_state=auth_state,
        headers={
            "Content-Type": "application/json",
        },
        json={"prompt": question},
        timeout=120,
    )
    return response

def main():
    auth_state = {"token": authenticate(force_refresh=False)}
    print("Authenticated successfully. Token retrieved.")

    # questions = load_questions("natural_products")
    questions = ["Start from C[C@H](c1ccc(cc1)F)NC(=O)c2cc(cc(c2)OS(=O)(=O)Cc3ccccc3)OCC(=O)NCCCCCN. Generate 200 analogues and optimize for binding to protein P56817. Use a batch size of 20 and run for 5 iterations"]
    print(f"Loaded {len(questions)} questions.")

    results = []
    for i, question in enumerate(questions, start=1):
        print(f"Submitting question {i}/{len(questions)}: {question[:50]}...")
        response = submit_question(auth_state, question)
        if response.status_code == 200:
            print(f"Question {i} submitted successfully.")
            results.append(response.json())
        else:
            print(f"Failed to submit question {i}: {response.text}")

    results_file = Path("results.json")
    with results_file.open("w") as file:
        json.dump(results, file, indent=4)
    print(f"Results saved to {results_file}")

if __name__ == "__main__":
    main()