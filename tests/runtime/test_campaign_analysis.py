"""Scientific provenance, plotting, and shared presentation contracts."""
import base64
from pathlib import Path
from types import SimpleNamespace
import pytest
from sable_runtime.registry import SkillRegistry
from sable_runtime.local_worker import run_worker
from sable_runtime.presentation import validate_presentation
from sable_runtime.campaigns import compact


def skill():
    registry = SkillRegistry()
    registry.install(Path(__file__).resolve().parents[2] / 'skills/campaign-analysis')
    package = registry.get('campaign-analysis', '1.0.0')
    return registry, package


def observation(smiles, value, candidate):
    return {'candidate_id':candidate,'canonical_smiles':smiles,'method':'RDKit','method_version':'test','source':'computed','values':[{'property':'tpsa','value':value,'unit':'angstrom^2'}]}


def test_real_figures_and_reference_resolution():
    registry, package = skill()
    results = {'measure':{'library_action_id':'library','observations':[observation('CCO',20,'a'),observation('CCN',25,'b'),observation('CCC',0,'c')]},
               'batch':{'kind':'recommendation','library_action_id':'library','round':2,'candidates':[{'id':name} for name in ('a','b','c')]}}
    context = SimpleNamespace(results=results,run_worker=lambda name,payload:run_worker(package,name,payload))
    for kind in ('distribution','progress','chemical_space'):
        result=registry.handler(package,package.manifest.operations[0])({'measurement_action_ids':['measure'],'property':'tpsa','chart':kind,'direction':'MIN'},context)
        validate_presentation(result['presentation'])
        assert base64.b64decode(result['figure_png_base64']).startswith(b'\x89PNG\r\n')
        assert result['source_action_ids']==['measure'] and result['summary']['count']==3
        assert compact(result)['figure_png_base64']=={'format':'png','available':True}
        if kind=='progress':
            assert result['presentation']['blocks'][0]['points']==[{'x':2,'y':0.0}]


def test_incompatible_and_duplicate_sources_rejected():
    registry, package=skill()
    operation=registry.handler(package,package.manifest.operations[0])
    first=observation('CCO',20,'a');second=observation('CCN',25,'b');second['values'][0]['unit']='nm^2'
    context=SimpleNamespace(results={'m':{'observations':[first,second]}},run_worker=lambda *args:pytest.fail('Do not plot incompatible units'))
    args={'measurement_action_ids':['m'],'property':'tpsa','chart':'distribution','direction':'MIN'}
    with pytest.raises(ValueError,match='shared method'):
        operation(args,context)
    context.results['m']['observations']=[first,first]
    with pytest.raises(ValueError,match='Repeated'):
        operation(args,context)
    context.results['m']['observations']=[first]
    with pytest.raises(ValueError,match='round'):
        operation({**args,'chart':'progress'},context)


def test_portable_schema_matches_published_contract():
    import json
    from sable_runtime.presentation import PRESENTATION_SCHEMA
    path=Path(__file__).resolve().parents[2]/'schemas/result-presentation.schema.json'
    assert json.loads(path.read_text())==PRESENTATION_SCHEMA
