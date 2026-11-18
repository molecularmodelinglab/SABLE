"""Scientific contracts plus real local enumeration/optimization. Docker only."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
pytest.importorskip('baybe')
pytest.importorskip('healer')

from sable_runtime.contracts import Action, Budget
from sable_runtime.executor import Executor, ExecutionError
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from baseline_worker import baseline_worker
from sable_runtime.science import filter_library, measurements

SKILLS = Path(__file__).resolve().parents[2] / 'skills'


def registry():
    result = SkillRegistry()
    for name in ('healer', 'baybe', 'rdkit-descriptors', 'hit-to-lead'):
        result.install(SKILLS / name)
    return result


def setup(tmp_path):
    reg = registry()
    ledger = Ledger(tmp_path / 'science.sqlite')
    ledger.create_campaign('science', 'alice', Budget(max_actions=24, max_units=32))
    grants = {(name, version, op.id) for name, version in [('healer','1.1.0'),('baybe','1.1.0'),('rdkit-descriptors','1.2.0')]
              for op in reg.get(name, version).manifest.operations}
    executor = Executor(reg, ledger, grants={'alice': grants})
    def execute(skill, operation, action_id, inputs):
        version = '1.2.0' if skill == 'rdkit-descriptors' else '1.1.0'
        return executor.execute(Action(campaign_id='science', action_id=action_id, skill_id=skill,
                                skill_version=version, skill_digest=reg.get(skill, version).digest,
                                operation_id=operation, inputs=inputs), owner='alice')
    return execute, ledger, reg


def seed_result(ledger, action_id, result):
    # Trusted test fixture, representing an already successful operation, not model input.
    action = SimpleNamespace(campaign_id='science', action_id=action_id)
    ledger.claim(action, 'alice', action_id, 1, {})
    ledger.finish(action, 'alice', 'succeeded', result)


def fixed_library():
    candidates, exclusions = filter_library(['CCO', 'CCCO', 'CCCCO', 'CCN', 'CCCN', 'CCCCN', 'CCOC', 'CCOCC'], 'CO', {})
    return {'kind':'candidate_library', 'starting_smiles':'CO', 'candidates':candidates,
            'constraints':{'scaffold_smarts':None,'property_filters':[]}, 'exclusions':exclusions,
            'provenance':{'tool':'fixture'}}


def objectives(strategy):
    targets = [{'name':'qed','mode':'MAX','bounds':[0,1]}]
    if strategy != 'single':
        targets.append({'name':'molecular_weight','mode':'MIN','bounds':[20,200]})
    return targets


def test_hard_constraints_deduplication_and_seed_exclusion():
    constraints = {'scaffold_smarts':'c1ccccc1','property_filters':[{'property':'molecular_weight','minimum':90,'maximum':150}]}
    accepted, excluded = filter_library(['c1ccccc1','Oc1ccccc1','c1ccc(O)cc1','CCCC','invalid','CCCCCCCCCCc1ccccc1'], 'c1ccccc1', constraints)
    assert len(accepted) == 1 and accepted[0]['smiles'] == 'Oc1ccccc1'
    assert {e['reason'] for e in excluded} == {'seed_or_duplicate','scaffold_constraint','invalid_structure','property_constraint'}
    with pytest.raises(ValueError):
        filter_library([], 'CCO', constraints)


def test_workflow_skill_is_instruction_only():
    reg = registry()
    description = reg.describe('hit-to-lead','1.1.0',{('hit-to-lead','1.1.0')})
    assert not description['manifest']['operations']
    assert reg.get('hit-to-lead', '1.1.0').code == b''


@pytest.mark.parametrize('strategy', ['single','pareto','desirability'])
def test_real_baybe_rounds_referenced_rdkit_and_ranking(tmp_path, strategy):
    execute, ledger, reg = setup(tmp_path)
    library = fixed_library()
    seed_result(ledger, 'library', library)
    spec = {'library_action_id':'library','measurement_action_ids':[],'targets':objectives(strategy),
            'strategy':strategy,'max_rounds':2,'batch_size':2,'seed':42}
    first = execute('baybe','recommend','batch-1',spec)
    assert len(first['candidates']) == 2
    measured1 = execute('rdkit-descriptors','calculate','measure-1',
                        {'source_action_id':'batch-1','properties':[t['name'] for t in spec['targets']]})
    assert measured1['library_action_id'] == 'library'
    spec['measurement_action_ids'] = ['measure-1']
    second = execute('baybe','recommend','batch-2',spec)
    assert len(second['candidates']) == 2
    assert not {c['id'] for c in first['candidates']} & {c['id'] for c in second['candidates']}
    execute('rdkit-descriptors','calculate','measure-2',{'source_action_id':'batch-2','properties':[t['name'] for t in spec['targets']]})
    spec['measurement_action_ids'].append('measure-2')
    stopped = execute('baybe','recommend','batch-3',spec)
    assert stopped['stop_reason'] == 'round_budget' and not stopped['candidates']
    report = execute('baybe','rank','rank', {k:spec[k] for k in ('library_action_id','measurement_action_ids','targets','strategy')})
    assert report['evaluated_candidates'] == 4
    assert all(row['complete'] for row in report['rankings'])
    assert {r['molecule_id'] for r in report['rankings']} == {c['id'] for c in first['candidates'] + second['candidates']}
    # For single/Pareto, the default adapter path and skill match under the same initial seed.
    if strategy in {'single','pareto'}:
        baseline = baseline_worker( {'targets':spec['targets'],'strategy':strategy,'batch_size':2,
                         'search_space':{c['id']:c['smiles'] for c in library['candidates']},'measurements':[],'seed':42,'legacy':True})
        assert baseline['recommended_ids'] == [c['id'] for c in first['candidates']]


def test_outstanding_batches_and_scope_are_enforced(tmp_path):
    execute, ledger, _ = setup(tmp_path)
    seed_result(ledger, 'library', fixed_library())
    seed_result(ledger, 'other-library', fixed_library())
    spec = {'library_action_id':'library','measurement_action_ids':[], 'targets':objectives('single'),
            'strategy':'single','batch_size':1,'max_rounds':2}
    execute('baybe','recommend','batch-1',spec)
    with pytest.raises(ExecutionError):
        execute('baybe','recommend','unmeasured-next',spec)
    measured = execute('rdkit-descriptors','calculate','measure',{'source_action_id':'batch-1','properties':['qed']})
    with pytest.raises(ExecutionError):
        execute('baybe','recommend','wrong-scope',{**spec,'library_action_id':'other-library','measurement_action_ids':['measure']})
    with pytest.raises(ExecutionError):
        execute('baybe','recommend','changed-budget',{**spec,'max_rounds':3,'measurement_action_ids':['measure']})
    with pytest.raises(ExecutionError):
        execute('baybe','recommend','invented',{**spec,'measurements':[{'qed':0.99}]})
    with pytest.raises(ExecutionError):
        execute('rdkit-descriptors','calculate','unknown-source',{'source_action_id':'someone-elses-action','properties':['qed']})


def test_measurement_units_identity_and_versions():
    library = fixed_library()
    candidate = library['candidates'][0]
    obs = {'candidate_id':candidate['id'],'canonical_smiles':candidate['smiles'], 'source':'computed',
           'method':'RDKit','method_version':'test','values':[{'property':'qed','value':0.4,'unit':'dimensionless'}]}
    result = {'library_action_id':'library','observations':[obs]}
    for field, bad in [('canonical_smiles','N'),('source','predicted-by-llm'),('method','unknown')]:
        altered = deepcopy(result); altered['observations'][0][field] = bad
        with pytest.raises(ValueError):
            measurements({'library':library,'measurement':altered},'library',['measurement'],objectives('single'))
    altered = deepcopy(result); altered['observations'][0]['values'][0]['unit'] = 'kcal/mol'
    with pytest.raises(ValueError):
        measurements({'library':library,'measurement':altered},'library',['measurement'],objectives('single'))


def test_real_healer_enumeration_preserves_constraints(tmp_path):
    execute, _, _ = setup(tmp_path)
    library = execute('healer','enumerate','library',{'starting_smiles':'CC(=O)Nc1ccccc1',
                      'max_candidates':32,'n_compositions':4,'scaffold_smarts':'c1ccccc1',
                      'property_filters':[{'property':'molecular_weight','minimum':80,'maximum':500}],'seed':42})
    assert library['candidates'], 'Packaged building-block fixture should yield analogues'
    assert library['provenance']['reaction_rows']
    assert len(library['candidates']) <= 32
    from rdkit import Chem
    scaffold = Chem.MolFromSmarts('c1ccccc1')
    assert all(Chem.MolFromSmiles(c['smiles']).HasSubstructMatch(scaffold) for c in library['candidates'])
    batch = execute('baybe','recommend','batch',{'library_action_id':'library','measurement_action_ids':[],
                      'targets':objectives('single'),'strategy':'single','batch_size':2,'max_rounds':1})
    measurements_result = execute('rdkit-descriptors','calculate','measure',{'source_action_id':'batch','properties':['qed']})
    assert measurements_result['observations']


def test_stereospecific_scaffold_is_enforced():
    candidates, excluded = filter_library(['F[C@@H](Cl)Br'], 'F[C@H](Cl)Br',
                                          {'scaffold_smarts':'F[C@H](Cl)Br'})
    assert not candidates
    assert excluded == [{'source_index':0,'reason':'scaffold_constraint'}]


def test_result_references_cannot_cross_owners(tmp_path):
    execute, ledger, _ = setup(tmp_path)
    ledger.create_campaign('bob-campaign','bob',Budget(max_actions=2,max_units=2))
    action = SimpleNamespace(campaign_id='bob-campaign',action_id='foreign-library')
    ledger.claim(action,'bob','fingerprint',1,{})
    ledger.finish(action,'bob','succeeded',fixed_library())
    with pytest.raises(ExecutionError):
        execute('rdkit-descriptors','calculate','stolen',{'source_action_id':'foreign-library','properties':['qed']})
