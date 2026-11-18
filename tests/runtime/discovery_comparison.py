"""Compare default adapter and skill recommendations on a frozen HEALER library."""
import contextlib
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile
import time

from test_discovery import setup, objectives
from baseline_worker import baseline_worker
from sable_runtime.science import measurements


def main():
    with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(sys.stderr):
        execute, ledger, _ = setup(Path(folder))
        start = time.perf_counter()
        library = execute('healer', 'enumerate', 'library', {
            'starting_smiles':'CC(=O)Nc1ccccc1','max_candidates':32,'n_compositions':4,
            'scaffold_smarts':'c1ccccc1','seed':42,
            'property_filters':[{'property':'molecular_weight','minimum':80,'maximum':500}]})
        if len(library['candidates']) < 4:
            raise ValueError('Comparison needs at least four eligible fixture candidates')
        enumeration_seconds = time.perf_counter() - start
        cases = []
        for strategy in ('single','pareto'):
            refs = []
            inputs = {'library_action_id':'library','measurement_action_ids':refs,'targets':objectives(strategy),
                      'strategy':strategy,'batch_size':2,'max_rounds':2,'seed':42}
            rounds = []
            for index in range(2):
                records = {e['action']:e['payload'] for e in ledger.events('science','alice') if e['kind']=='action_succeeded'}
                _, rows, _ = measurements(records,'library',refs,inputs['targets'])
                start = time.perf_counter()
                baseline = baseline_worker( {'targets':inputs['targets'],'strategy':strategy,'batch_size':2,
                                  'search_space':{c['id']:c['smiles'] for c in library['candidates']},
                                  'measurements':rows,'seed':42,'legacy':True})
                baseline_seconds = time.perf_counter() - start
                # Different objective strategies need independent library optimization scopes.
                # Set up a separate campaign per strategy, retaining this exact frozen library.
                selected = execute('baybe','recommend',f'{strategy}-batch-{index}',inputs)
                ids = [c['id'] for c in selected['candidates']]
                if ids != baseline['recommended_ids']:
                    raise AssertionError('v1/v2 recommendation parity failed')
                measurement_id = f'{strategy}-measure-{index}'
                observed = execute('rdkit-descriptors','calculate',measurement_id,
                                   {'source_action_id':f'{strategy}-batch-{index}','properties':[t['name'] for t in inputs['targets']]})
                refs.append(measurement_id)
                rounds.append({'round':index+1,'v1_recommended_ids':baseline['recommended_ids'],
                               'v2_recommended_ids':ids,'v1_seconds':baseline_seconds,'observations':observed})
            ranked = execute('baybe','rank',f'{strategy}-rank', {k:inputs[k] for k in ('library_action_id','measurement_action_ids','targets','strategy')})
            cases.append({'strategy':strategy,'rounds':rounds,'ranking':ranked})
            # New ledger/campaign avoids mixing fixed optimization specifications.
            if strategy == 'single':
                from test_discovery import seed_result
                next_folder = Path(folder) / 'pareto'; next_folder.mkdir()
                execute, ledger, _ = setup(next_folder)
                seed_result(ledger,'library',library)
        from healer.domain import resolve_bb_path
        fixture = Path(resolve_bb_path('test'))
        evidence = {'scope':'v1 adapter vs v2 skills; same frozen eligible library, seed and evaluation budget; not a full v1 graph benchmark',
                    'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    'versions':{p:importlib.metadata.version(p) for p in ('mol-healer','baybe','rdkit')},
                    'enumeration_seconds':enumeration_seconds,'library':library,'cases':cases}
    print(json.dumps(evidence, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
