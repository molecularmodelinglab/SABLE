"""Enumeration breadth, deployment limits and candidate-count accounting."""
from types import SimpleNamespace
from pathlib import Path
import importlib.util
import json
import pytest
from jsonschema import validate, ValidationError
from sable_runtime.enumeration import enumeration_settings, yield_summary

ROOT = Path(__file__).resolve().parents[2] / 'skills'


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    for name in ('HEALER_MAX_TOTAL_PRODUCTS','HEALER_N_COMPOSITIONS','HEALER_MAX_BBS_PER_FRAG','SABLE_MAX_ENUMERATION_PRODUCTS'):
        monkeypatch.delenv(name, raising=False)


def load(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),ROOT/name/'operations.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_default_breadth_is_not_evaluation_budget():
    assert enumeration_settings({}) == {'max_total_products':8000,'n_compositions':100,'max_bbs_per_frag':10}
    assert enumeration_settings({'batch_size':3,'max_rounds':2}) == enumeration_settings({})
    assert enumeration_settings({'max_candidates':128})['max_total_products']==128


def test_host_defaults_explicit_overrides_and_ceiling(monkeypatch):
    monkeypatch.setenv('HEALER_MAX_TOTAL_PRODUCTS','12000')
    monkeypatch.setenv('HEALER_N_COMPOSITIONS','150')
    monkeypatch.setenv('HEALER_MAX_BBS_PER_FRAG','20')
    assert enumeration_settings({})=={'max_total_products':12000,'n_compositions':150,'max_bbs_per_frag':20}
    assert enumeration_settings({'max_total_products':500,'n_compositions':8,'max_bbs_per_frag':5})=={'max_total_products':500,'n_compositions':8,'max_bbs_per_frag':5}
    monkeypatch.setenv('SABLE_MAX_ENUMERATION_PRODUCTS','1000')
    with pytest.raises(ValueError):enumeration_settings({'max_total_products':1001})
    with pytest.raises(ValueError):enumeration_settings({})


@pytest.mark.parametrize('inputs',[{'max_total_products':0},{'max_total_products':True},{'max_total_products':20001},
    {'max_total_products':12,'max_candidates':12},{'n_compositions':1001},{'max_bbs_per_frag':101}])
def test_invalid_breadth_fails_before_submission(inputs):
    with pytest.raises(ValueError):enumeration_settings(inputs)


@pytest.mark.parametrize('name',['healer','healer-api'])
def test_packages_accept_broad_library_and_forward_settings(name):
    module=load(name)
    manifest=json.loads((ROOT/name/'manifest.json').read_text())
    inputs={'starting_smiles':'CCO','max_total_products':8000,'n_compositions':100,'max_bbs_per_frag':10}
    validate(inputs,manifest['operations'][0]['input_schema'])
    validate({'starting_smiles':'CCO'},manifest['operations'][0]['input_schema'])
    calls=[]
    def capture(worker,payload):
        calls.append(payload)
        return {'kind':'remote_job','job_id':'test','status':'queued'} if name=='healer-api' else {'products':['CCCO','CCCO','CCO'],'rows':[],'version':'test','source_count':3}
    context=SimpleNamespace(submit_job=capture,run_worker=capture)
    output=(module.enumerate if name=='healer-api' else module.enumerate_analogs)(inputs,context)
    assert all(calls[0][key]==inputs[key] for key in ('max_total_products','n_compositions','max_bbs_per_frag'))
    if name=='healer':
        validate(output,manifest['operations'][0]['output_schema'])
        assert output['enumeration']['eligible_count']==1
        assert output['enumeration']['exclusion_counts']=={'seed_or_duplicate':2}
    with pytest.raises(ValidationError):validate({**inputs,'batch_size':3},manifest['operations'][0]['input_schema'])


def test_yield_accounts_for_truncation_and_exclusions():
    counts=yield_summary(4,7,4,[{'id':'a'}],[{'reason':'seed_or_duplicate'}]*3)
    assert counts['truncated_count']==3
    assert counts['excluded_count']==3 and counts['eligible_count']==1
    assert counts['capacity_reached'] is True


def test_local_backend_filtering_counts_are_not_lost(monkeypatch):
    import sys
    spec=importlib.util.spec_from_file_location('healer_worker',ROOT/'healer/worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    class Tool:
        def __init__(self, **kwargs):self._enumerator=SimpleNamespace(get_results=lambda **kw:[1,2,3])
        def _run(self, **kwargs):return 'No molecules were generated that met the criteria.'
    monkeypatch.setitem(sys.modules,'tools.healer_enumerator_tool',SimpleNamespace(HealerEnumeratorTool=Tool))
    result=worker.run({'mode':'MoleculeHEALER','stock':'US_stock','reaction_tags':['all'],
        'max_bbs_per_frag':10,'max_total_products':8000,'starting_smiles':'CCO','n_compositions':100,'seed':42})
    assert result['products']==[]
    assert result['source_count']==result['backend_filtered_count']==3
