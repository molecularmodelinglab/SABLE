"""Local foundation demo; execute in the runtime Docker image."""
import argparse
import json
from pathlib import Path
from .contracts import Action, Budget
from .executor import Executor
from .ledger import Ledger
from .registry import SkillRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skill', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--campaign', default='demo')
    args = parser.parse_args()
    registry = SkillRegistry()
    digest = registry.install(args.skill)
    ledger = Ledger(args.ledger)
    ledger.create_campaign(args.campaign, 'demo-user', Budget(max_actions=2, max_units=2))
    executor = Executor(registry, ledger, grants={
        'demo-user': frozenset({('dataset-summary', '1.0.0', 'summarize')})})
    action = Action(campaign_id=args.campaign, action_id='summary', skill_id='dataset-summary',
                    skill_version='1.0.0', skill_digest=digest, operation_id='summarize',
                    inputs={'values': [1, 2, 3], 'unit': 'mg/mL'})
    print(json.dumps({'result': executor.execute(action, owner='demo-user'),
                      'events': ledger.events(args.campaign, 'demo-user')}, indent=2))


if __name__ == '__main__':
    main()
