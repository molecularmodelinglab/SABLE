"""Poll durable remote jobs independently of model turns and browser sessions."""
import argparse
import json
import os
from pathlib import Path
import signal
from threading import Event
from .jobs import JobManager
from .ledger import Ledger
from .registry import SkillRegistry
from server.services.campaign_credentials import load_campaign_services as load_services
from .skill_store import load_deployment_skills, deployment_revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    path = Path(os.environ.get('SABLE_V2_LEDGER_PATH', ''))
    if not path.is_absolute():
        parser.error('SABLE_V2_LEDGER_PATH must be an absolute durable local path')
    path.parent.mkdir(parents=True, exist_ok=True)
    services, credentials = load_services(os.getenv('SABLE_SERVICE_BINDINGS_PATH'))
    revision = deployment_revision()
    registry, allowed, _ = load_deployment_skills(Path(__file__).resolve().parents[1] / 'skills', services)
    jobs = JobManager(Ledger(path), registry, services=services, credentials=credentials, allowed=allowed)
    stopped = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    while not stopped.is_set():
        current_revision = deployment_revision()
        if current_revision != revision:
            registry, allowed, _ = load_deployment_skills(Path(__file__).resolve().parents[1] / 'skills', services)
            jobs.registry, jobs.allowed = registry, allowed
            revision = current_revision
        outcomes = jobs.drive_due(limit=1)
        if outcomes:
            print(json.dumps(outcomes), flush=True)
        if args.once:
            break
        stopped.wait(2)


if __name__ == '__main__':
    main()
