import requests
import json
import time
import os
from pathlib import Path
from typing import Optional

BASE_URL = "http://localhost:8000"

EMAIL = os.environ.get("SABLE_EMAIL", "dummyuser@gmail.com")
PASSWORD = os.environ.get("SABLE_PASSWORD", "@DummyUserPA22")


POLL_INTERVAL = 10 
MAX_POLL_TIME = 3600*4  # maximum time to wait for a run (3 hour)
TOKEN_CACHE_FILE = Path(".sable_auth_token.json")


def _load_cached_token() -> Optional[str]:
    """Load a previously cached access token, if available."""
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
    """Persist access token locally for reuse between runs."""
    try:
        TOKEN_CACHE_FILE.write_text(
            json.dumps({"access_token": token}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"⚠️  Failed to persist auth token cache: {exc}")


def authenticate(force_refresh: bool = False) -> str:
    """Authenticate and return access token, optionally forcing refresh."""
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
    """
    Make an authenticated request and automatically refresh token on 401/403.
    """
    token = auth_state.get("token")
    if not token:
        token = authenticate(force_refresh=False)
        auth_state["token"] = token

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
            print("\n🔐 Auth token expired/invalid. Refreshing login...")
            auth_state["token"] = authenticate(force_refresh=True)
            continue

        return response

    return response


def load_questions(qtype="general"):
    """Load questions from questions.py file."""
    from questions import questions
    return questions[qtype]


def submit_question(auth_state: dict, question: str) -> Optional[dict]:
    """Submit a question and return the response."""
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
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to submit question: {response.text}")
        return None


def get_run_status(auth_state: dict, run_id: str) -> Optional[dict]:
    """Get the status of a run."""
    response = authenticated_request(
        method="GET",
        path=f"/runs/{run_id}",
        auth_state=auth_state,
        timeout=60,
    )
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to get run status: {response.text}")
        return None


def wait_for_completion(auth_state: dict, run_id: str, question_num: int, total: int) -> dict:
    """
    Poll the run status until it completes or fails.
    
    Returns the final run status.
    """
    print(f"  Waiting for completion of question {question_num}/{total}...")
    start_time = time.time()
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed > MAX_POLL_TIME:
            print(f"  ⚠️  Timeout waiting for run {run_id} to complete (max {MAX_POLL_TIME}s)")
            break
        
        status_data = get_run_status(auth_state, run_id)
        if not status_data:
            print(f"  ⚠️  Failed to get status for run {run_id}")
            time.sleep(POLL_INTERVAL)
            continue
        
        status = status_data.get("status", "unknown")
        current_iteration = status_data.get("current_iteration", 0)
        max_iterations = status_data.get("max_iterations", 0)
        
        elapsed_mins = int(elapsed / 60)
        elapsed_secs = int(elapsed % 60)
        print(f"  Status: {status} | Iteration: {current_iteration}/{max_iterations} | "
              f"Elapsed: {elapsed_mins}m {elapsed_secs}s", end='\r')
        
        if status in ["completed", "failed", "error"]:
            print()
            if status == "completed":
                print(f"  ✓ Question {question_num}/{total} completed successfully!")
                if status_data.get("best_molecules"):
                    best_score = status_data["best_molecules"][0].get("score", "N/A")
                    print(f"    Best score: {best_score}")
            else:
                print(f"  ✗ Question {question_num}/{total} {status}")
                if status_data.get("error"):
                    print(f"    Error: {status_data['error']}")
            return status_data
        
        time.sleep(POLL_INTERVAL)
    
    return status_data or {"status": "timeout", "run_id": run_id}


def main():
    """Main function to submit questions sequentially."""
    auth_state = {"token": authenticate(force_refresh=False)}
    print("✓ Authenticated successfully\n")

    # questions_list = load_questions("natural_products")
    questions_list = [
        # "Starting from O=C(Cc1c[nH]c2ccccc12)N1CCN(CC2(c3ccccc3)CC2)CC1, I need to find molecules that with better affinity to P03372. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from COC(=O)C1C2CCCC2CN1C(=O)Cc1ccc2cn[nH]c2c1, I need to find molecules that with better affinity to P03372. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from COc1ccc(NC(=O)c2cc3ccccc3cc2O)cc1-c1nn[nH]n1, I need to find molecules that with better affinity to Q92769. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from O=C(NCc1ccccc1OC1CCC1)c1ccc2[nH]ncc2c1, I need to find molecules that with better affinity to Q92731. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from Cc1nc(Cl)ccc1C(=O)NCC(=O)Nc1cccc2c(=O)[nH][nH]c(=O)c12, I need to find molecules that with better affinity to P29274. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from Cc1sc2nc(SCc3cc(C(C)(C)C)on3)nc(N)c2c1C, I need to find molecules that with better affinity to P56817. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from O=C(Cn1c(=O)[nH]c(=O)c2ccccc21)NCc1ccc(OCC(=O)Nc2ccc(Cl)cc2)cc1, I need to find molecules that with better affinity to P04150. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from O=C(CCc1c[nH]c2ccccc12)N1CCc2[nH]nc(O)c2CC1, I need to find molecules that with better affinity to P30291. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from CC(C)c1c[nH]nc1C(=O)NCCCc1nc2ccccc2[nH]1, I need to find molecules that with better affinity to P21731. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from COc1ccccc1N(C)C(=O)Cc1c[nH]c2ccccc12, I need to find molecules that with better affinity to O97775. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from CC(=O)OCCc1cccc(NC(=O)c2c(O)cccc2F)c1, I need to find molecules that with better affinity to Q9BY41. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from CC(=O)NCc1ccc(-c2csc(NC(=O)Cc3c(C)[nH]c4ccc(F)cc34)n2)cc1, I need to find molecules that with better affinity to P35968. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from Fc1ncccc1CSc1ncnc2sc3c(c12)CCC3, I need to find molecules that with better affinity to Q00534. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from O=C1Nc2ccccc2OC[C@@H]1NCc1cc(Cl)c2c(c1)OCCCO2, I need to find molecules that with better affinity to P37231. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from O=C(Cc1c[nH]c2ccccc12)N1CCN(CC2(c3ccccc3)CC2)CC1, I need to find molecules that with better affinity to P03372. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",

        # "Starting from Cc1nc(C(=O)Nc2nc(-c3ccc4c(c3)CCC(=O)N4)cs2)ccc1C#N, I need to find molecules that with better affinity to Q96RR4. Enumerate 200 compounds, and do 10 Bayesian optimisation iterations. Use a batch size of 20",
    #    "Starting from this compound 'CC1(C)CCN(CC1)CC2=CC=C(C=C2)C(=O)NCC3(CCCN(C3)C4=CC(=NC=N4)NC)O' I need to find molecules that optimize for binding affinity to this target Q86U44. Do 10 iterations and use a batch size of 10"
        #  "Starting from this compound 'COC(=O)C1CCCN(c2cc(-n3cc(C4(C)CCN(C(=O)c5ccc(CBr)cc5)CC4)nn3)ncn2)C1' I need to find molecules that optimize for binding affinity to this target Q86U44. Do 10 iterations and use a batch size of 10"
        # "Starting from this compound 'C=1(N=C(C=C(N=1)NC)N2(CCC3(CC2)(NC(CN(C3)C=4(C=C(C(=CC=4F)CN5(CCC(CC5)(C)C))F))=O)))' I need to find molecules that optimize for binding affinity to this target Q86U44. Do 10 iterations and use a batch size of 10"
      #"Starting from this compound 'CCC/N=C1\S/C(=C\c2ccc(C(=O)OCc3oc(=O)oc3C)cc2)C(=O)N1c1ccccc1O' I need to find molecules that optimize for binding affinity to this target P21453. Do 10 iterations and use a batch size of 10"
       #"Starting from this compound 'COC1=CC=C(C=C1)C2=NN(C=C2/C=N/NC3=CC=C(C=C3)S(=O)(=O)N)C4=CC=C(C=C4)S(=O)(=O)N' I need to find molecules that optimize for binding affinity to this target O43570. Do 10 iterations and use a batch size of 10",
       #"Starting from this compound 'COC1=CC(=CC(=C1OC)OC)/C=N\C2CCN(CC3=CC=CC=C3)C2' I need to find molecules that optimize for binding affinity to this target P56817. Do 10 iterations and use a batch size of 10",
    #   "Starting from this compound 'COc1cc(/C=N\C2CCN(Cc3ccccc3)C2)cc(OC)c1OC' I need to find molecules that optimize for binding affinity to this target P56817. Do 10 iterations and use a batch size of 10",
    #   "Starting from this compound 'COc1ccc(-c2nn(-c3ccc(S(N)(=O)=O)cc3)cc2/C=N/Nc2ccc(S(N)(=O)=O)cc2)cc1' I need to find molecules that optimize for binding affinity to this target O43570. Do 10 iterations and use a batch size of 10"

    # "I would like to optimize C1C=CC(N2CCN(C3SC=C(C(NCC4C=C(C)C=CC=4)=O)N=3)CC2)=CC=1 for better binding affinity to P30838.  Do 10 iterations and use a batch size of 15.",
    # "Optimize C1C=CC(N2CCN(C3SC=C(C(NCC4C=C(C5CC5)C=CC=4)=O)N=3)CC2)=CC=1C(F)(F)F for better binding affinity to P30838.  Do 2 iterations and use a batch size of 3.",
    # "Optimize C1C=CC(N2CCN(C3SC=C(C(NCC4C=C(C5=CSC(C)=N5)C=CC=4)=O)N=3)CC2)=CC=1C(F)(F)F for better binding affinity to P30838.  Do 2 iterations and use a batch size of 3.",

    # "Optimize Fc1ccc(CNC2CCN(Cc3ccccc3)C2)c(F)c1 for better binding affinity to P56817.  Do 2 iterations and use a batch size of 3.",
    # "Optimize CC(=O)Nc1nnc(S(N)(=O)=O)s1 for better binding affinity to O43570.  Do 2 iterations and use a batch size of 3.",
    # "Optimize Cc1cn(-c2cc(NC(=O)c3ccc(C)c(Nc4nccc(-c5cccnc5)n4)c3)cc(C(F)(F)F)c2)cn1 for better binding affinity to P00519.  Do 2 iterations and use a batch size of 3.",
    # "Optimize CCC/N=C1\S/C(=C\c2ccc(C(=O)OC)c(Cl)c2)C(=O)N1c1ccccc1C for better binding affinity to P21453.  Do 2 iterations and use a batch size of 3.",
    # "Optimize Aspirin for maximum QED",
    "Optimize Aspirin for maximum affinity to P00519.  Do 2 iterations and use a batch size of 3.",

    ]

    
    print(f"Loaded {len(questions_list)} question(s) to run sequentially\n")
    print("=" * 80)

    results = []
    
    for i, question in enumerate(questions_list, start=1):
        print(f"\n[{i}/{len(questions_list)}] Submitting question:")
        print(f"  {question[:100]}{'...' if len(question) > 100 else ''}")
        
        # Submit the question
        response = submit_question(auth_state, question)
        if not response:
            print(f"  ✗ Failed to submit question {i}")
            results.append({
                "question_num": i,
                "question": question,
                "status": "submission_failed",
                "error": "Failed to submit"
            })
            continue
        
        run_id = response.get("run_id")
        print(f"  ✓ Submitted successfully (Run ID: {run_id})")
        
        final_status = wait_for_completion(auth_state, run_id, i, len(questions_list))
        
        result = {
            "question_num": i,
            "question": question,
            "run_id": run_id,
            "status": final_status.get("status"),
            "current_iteration": final_status.get("current_iteration"),
            "max_iterations": final_status.get("max_iterations"),
            "best_molecules": final_status.get("best_molecules", []),
            "error": final_status.get("error"),
        }
        results.append(result)
        
        print("  " + "-" * 76)
    
    print("\n" + "=" * 80)
    results_file = Path("results_sequential.json")
    with results_file.open("w") as file:
        json.dump(results, file, indent=4)
    
    print(f"\n✓ All {len(questions_list)} questions processed")
    print(f"✓ Results saved to {results_file}")
    
    completed = sum(1 for r in results if r.get("status") == "completed")
    failed = sum(1 for r in results if r.get("status") in ["failed", "error", "timeout", "submission_failed"])
    print(f"\nSummary: {completed} completed, {failed} failed")


if __name__ == "__main__":
    main()
