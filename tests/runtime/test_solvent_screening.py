"""Solvent-screening arithmetic, provenance, and input validation."""
from copy import deepcopy

import pytest

from sable_runtime.contracts import Action, Budget
from sable_runtime.executor import Executor, ExecutionError
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from sable_runtime.skill_validation import validate_package
from campaign_evaluation import ROOT, solvent_inputs


@pytest.fixture
def solvent(tmp_path):
    registry = SkillRegistry()
    package = validate_package(ROOT / 'skills/solvent-screening')
    registry.install(ROOT / 'skills/solvent-screening')
    ledger = Ledger(tmp_path / 'screen.sqlite')
    ledger.create_campaign('screen', 'alice', Budget(max_actions=20, max_units=20))
    executor = Executor(registry, ledger, grants={'alice': {('solvent-screening', '1.0.0', 'screen')}})
    counter = iter(range(20))
    def run(inputs):
        return executor.execute(Action(campaign_id='screen', action_id=f'screen-{next(counter)}',
            skill_id='solvent-screening', skill_version='1.0.0', skill_digest=package.digest,
            operation_id='screen', inputs=inputs), owner='alice')
    return run


def test_hansen_reference_arithmetic_and_provenance(solvent):
    data = solvent_inputs()
    result = solvent(data)
    # Independent hand-calculated fixture: dispersion delta 1 gives Ra 2,
    # polar delta 4 gives Ra 4, hydrogen-bonding delta 6 gives Ra 6.
    assert [row['distance'] for row in result['ranking']] == [2, 4, 6]
    assert [row['relative_energy_difference'] for row in result['ranking']] == [0.5, 1, 1.5]
    assert [row['sphere_position'] for row in result['ranking']] == ['inside', 'boundary', 'outside']
    assert result['target'] == data['target'] and result['temperature_k'] == 298.15
    assert result['radius_source'] == data['radius_source']
    assert result['excluded_ids'] == ['excluded']
    assert result['ranking'][0]['parameters']['source'] == 'synthetic A'


def test_screen_is_order_independent_and_respects_exclusions(solvent):
    data = solvent_inputs()
    original = deepcopy(data)
    first = solvent(data)
    data['candidates'].reverse()
    assert solvent(data) == first
    data['excluded_ids'] = [candidate['id'] for candidate in data['candidates']]
    assert solvent(data)['ranking'] == []
    assert original == solvent_inputs()


@pytest.mark.parametrize('mutation', ['zero-radius', 'nan', 'missing-source', 'unknown-exclusion', 'duplicate-id', 'wrong-units'])
def test_invalid_scientific_inputs_are_rejected(solvent, mutation):
    data = solvent_inputs()
    if mutation == 'zero-radius': data['radius'] = 0
    elif mutation == 'nan': data['target']['delta_d'] = float('nan')
    elif mutation == 'missing-source': del data['target']['source']
    elif mutation == 'unknown-exclusion': data['excluded_ids'] = ['unknown']
    elif mutation == 'duplicate-id': data['candidates'][1]['id'] = 'near'
    elif mutation == 'wrong-units': data['unit'] = 'cal/cm3'
    with pytest.raises(ExecutionError): solvent(data)
