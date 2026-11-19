"""Explicit bounded live-model/local-science smoke; invoke inside Docker only."""
from pathlib import Path
import json
import sys
from sable_runtime.agent_model import create_agent_model
from sable_runtime.campaigns import CampaignService
from sable_runtime.ledger import Ledger
from test_discovery import registry


def main():
    config = {}
    for line in Path('/tmp/sable.env').read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and not key.strip().startswith('#'):
            config[key.strip()] = value.strip().strip('"').strip("'")
    model = create_agent_model(config)
    try:
        service = CampaignService(Ledger('/tmp/live-discovery.sqlite'), registry(),
                    {('healer','1.1.0'),('baybe','1.1.0'),('rdkit-descriptors','1.2.0'),('hit-to-lead','1.2.0')})
        state = service.create('smoke-test',
            'Explore analogues of acetanilide, SMILES CC(=O)Nc1ccccc1, to maximize computed QED. '
            'Preserve the benzene substructure SMARTS c1ccccc1 and enforce molecular weight between 80 and 500 g/mol. '
            'Use the configured test building-block stock, at most 32 raw HEALER candidates, and two Bayesian optimization rounds with two evaluations each unless the eligible library is exhausted. '
            'Evaluate the starting compound separately if reporting improvement. Finish with the measured ranking, actual number evaluated, and limitations. This is a computational smoke test, not a biological efficacy claim.')
        for _ in range(20):
            if state['status'] != 'ready':
                break
            state = service.advance(state['id'], 'smoke-test', model)
        events = service.ledger.events(state['id'], 'smoke-test')
        report = {'model':model.identity,'status':state['status'],'turns':state['turns'],
                  'report':state['report'],'results':state['results'],'events':events}
        print(json.dumps(report, indent=2, allow_nan=False))
        has_ranking = any(r.get('kind') == 'optimization_report' and r.get('evaluated_candidates',0)>0 for r in state['results'].values())
        return 0 if state['status']=='completed' and has_ranking else 1
    finally:
        model.close()


if __name__ == '__main__':
    sys.exit(main())
