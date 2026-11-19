"""Explicit, bounded live smoke test. Run only inside Docker with a read-only config mount."""
import json
from pathlib import Path
import sys

from sable_runtime.agent_model import create_agent_model
from sable_runtime.campaigns import CampaignService
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry


def main():
    config = {}
    for line in Path('/tmp/sable.env').read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and not key.strip().startswith('#'):
            config[key.strip()] = value.strip().strip('"').strip("'")
    model = None
    try:
        model = create_agent_model(config)
        registry = SkillRegistry()
        registry.install(Path('/workspace/skills/rdkit-descriptors'))
        service = CampaignService(Ledger('/tmp/live-campaign.sqlite'), registry, {('rdkit-descriptors', '1.3.0')})
        state = service.create('smoke-test', 'Calculate molecular weight and QED for ethanol, SMILES CCO. Report the computed values with units and explain their limitations. No other properties or tools are needed.')
        for _ in range(6):
            if state['status'] != 'ready':
                break
            state = service.advance(state['id'], 'smoke-test', model)
        evidence = {'model': model.identity, 'status': state['status'], 'turns': state['turns'],
                    'report': state['report'], 'results': state['results'],
                    'events': service.ledger.events(state['id'], 'smoke-test')}
        print(json.dumps(evidence, indent=2))
        return 0 if state['status'] == 'completed' and state['results'] else 1
    except Exception:
        print(json.dumps({'status': 'unavailable', 'error': 'live_smoke_failed'}))
        return 1
    finally:
        if model is not None:
            model.close()


if __name__ == '__main__':
    sys.exit(main())
