import requests
import json
import time
from pathlib import Path
from typing import Optional

BASE_URL = "http://localhost:8000"

EMAIL = "dummyuser@gmail.com"
PASSWORD = "dummypassword"


POLL_INTERVAL = 10 
MAX_POLL_TIME = 3600*3  # maximum time to wait for a run (1 hour)


def authenticate():
    """Authenticate and return access token."""
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
    """Load questions from questions.py file."""
    from questions import questions
    return questions[qtype]


def submit_question(token: str, question: str) -> Optional[dict]:
    """Submit a question and return the response."""
    response = requests.post(
        f"{BASE_URL}/runs",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        },
        json={"prompt": question},
    )
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to submit question: {response.text}")
        return None


def get_run_status(token: str, run_id: str) -> Optional[dict]:
    """Get the status of a run."""
    response = requests.get(
        f"{BASE_URL}/runs/{run_id}",
        headers={
            "Authorization": f"Bearer {token}"
        },
    )
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to get run status: {response.text}")
        return None


def wait_for_completion(token: str, run_id: str, question_num: int, total: int) -> dict:
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
        
        status_data = get_run_status(token, run_id)
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
    
    # Return last known status if timeout
    return status_data or {"status": "timeout", "run_id": run_id}


def main():
    """Main function to submit questions sequentially."""
    token = authenticate()
    print("✓ Authenticated successfully\n")

    # questions_list = load_questions("natural_products")
    questions_list = [
        "Starting from COC(=O)C1C2CCCC2CN1C(=O)Cc1ccc2cn[nH]c2c1, I need to find molecules that with better affinity to P03372. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from COc1ccc(NC(=O)c2cc3ccccc3cc2O)cc1-c1nn[nH]n1, I need to find molecules that with better affinity to Q92769. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from O=C(NCc1ccccc1OC1CCC1)c1ccc2[nH]ncc2c1, I need to find molecules that with better affinity to Q92731. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from Cc1nc(Cl)ccc1C(=O)NCC(=O)Nc1cccc2c(=O)[nH][nH]c(=O)c12, I need to find molecules that with better affinity to P29274. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from Cc1sc2nc(SCc3cc(C(C)(C)C)on3)nc(N)c2c1C, I need to find molecules that with better affinity to P56817. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from O=C(Cn1c(=O)[nH]c(=O)c2ccccc21)NCc1ccc(OCC(=O)Nc2ccc(Cl)cc2)cc1, I need to find molecules that with better affinity to P04150. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from O=C(CCc1c[nH]c2ccccc12)N1CCc2[nH]nc(O)c2CC1, I need to find molecules that with better affinity to P30291. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from CC(C)c1c[nH]nc1C(=O)NCCCc1nc2ccccc2[nH]1, I need to find molecules that with better affinity to P21731. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from COc1ccccc1N(C)C(=O)Cc1c[nH]c2ccccc12, I need to find molecules that with better affinity to O97775. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from CC(=O)OCCc1cccc(NC(=O)c2c(O)cccc2F)c1, I need to find molecules that with better affinity to Q9BY41. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from CC(=O)NCc1ccc(-c2csc(NC(=O)Cc3c(C)[nH]c4ccc(F)cc34)n2)cc1, I need to find molecules that with better affinity to P35968. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from Fc1ncccc1CSc1ncnc2sc3c(c12)CCC3, I need to find molecules that with better affinity to Q00534. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from O=C1Nc2ccccc2OC[C@@H]1NCc1cc(Cl)c2c(c1)OCCCO2, I need to find molecules that with better affinity to P37231. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from O=C(Cc1c[nH]c2ccccc12)N1CCN(CC2(c3ccccc3)CC2)CC1, I need to find molecules that with better affinity to P03372. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",

        "Starting from Cc1nc(C(=O)Nc2nc(-c3ccc4c(c3)CCC(=O)N4)cs2)ccc1C#N, I need to find molecules that with better affinity to Q96RR4. Enumerate 200 compounds, and do 5 Bayesian optimisation iterations. Use a batch size of 20",
    ]
    
    print(f"Loaded {len(questions_list)} question(s) to run sequentially\n")
    print("=" * 80)

    results = []
    
    for i, question in enumerate(questions_list, start=1):
        print(f"\n[{i}/{len(questions_list)}] Submitting question:")
        print(f"  {question[:100]}{'...' if len(question) > 100 else ''}")
        
        # Submit the question
        response = submit_question(token, question)
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
        
        final_status = wait_for_completion(token, run_id, i, len(questions_list))
        
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
