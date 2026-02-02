import requests
import json
from pathlib import Path

BASE_URL = "http://localhost:8000"

EMAIL = "dummyuser@gmail.com"
PASSWORD = "dummypassword"

def authenticate():
    response = requests.post(
        f"{BASE_URL}/auth/login",
        headers={"Content-Type": "application/json"},
        json={"email": EMAIL, "password": PASSWORD},
    )
    if response.status_code == 200:
        return response.json().get("access_token")
    else:
        print("Authentication failed:", response.text)
        exit(1)

def load_questions(qtype="general"):
    from questions import questions
    questions = questions[qtype]
    return questions

def submit_question(token, question):
    response = requests.post(
        f"{BASE_URL}/runs",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        },
        json={"prompt": question},
    )
    return response

def main():
    token = authenticate()
    print("Authenticated successfully. Token retrieved.")

    # questions = load_questions("natural_products")
    questions = ["Start from C[C@H](c1ccc(cc1)F)NC(=O)c2cc(cc(c2)OS(=O)(=O)Cc3ccccc3)OCC(=O)NCCCCCN. Generate 200 analogues and optimize for binding to protein P56817. Use a batch size of 20 and run for 5 iterations"]
    print(f"Loaded {len(questions)} questions.")

    results = []
    for i, question in enumerate(questions, start=1):
        print(f"Submitting question {i}/{len(questions)}: {question[:50]}...")
        response = submit_question(token, question)
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