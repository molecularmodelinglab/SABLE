"""Create, validate, install, and select versioned skill packages."""
import argparse
import json
from pathlib import Path
import tempfile
from .contracts import Manifest
from .skill_store import SkillStore
from .skill_templates import create_package
from .skill_validation import validate_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Create an example package without overwriting files')
    init.add_argument('name'); init.add_argument('--output', required=True)
    init.add_argument('--template', choices=['inline','worker','remote','workflow'], default='inline')
    validate = commands.add_parser('validate', help='Check instructions, schemas, and Python entry points without executing code')
    validate.add_argument('directory')
    smoke = commands.add_parser('smoke', help='Execute one trusted operation in a disposable ledger; run inside Docker')
    smoke.add_argument('directory'); smoke.add_argument('--operation', required=True)
    smoke.add_argument('--inputs', required=True, help='JSON file containing operation inputs')
    install = commands.add_parser('install', help='Install a trusted immutable package; inactive by default')
    install.add_argument('directory'); install.add_argument('--store', required=True)
    install.add_argument('--activate', action='store_true')
    listing = commands.add_parser('list'); listing.add_argument('--store', required=True)
    for name in ('activate','disable','revoke'):
        command = commands.add_parser(name)
        command.add_argument('id'); command.add_argument('version'); command.add_argument('--store', required=True)
    commands.add_parser('schema', help='Print the manifest JSON schema')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = {'directory':str(create_package(args.name,args.output,args.template))}
        elif args.command == 'validate':
            skill = validate_package(args.directory)
            result = {'valid':True,'id':skill.manifest.id,'version':skill.manifest.version,'digest':skill.digest,
                      'scope':'Static validation only; provider behavior and scientific correctness require tests.'}
        elif args.command == 'install':
            result = SkillStore(args.store).install(args.directory,args.activate)
        elif args.command == 'smoke':
            from .contracts import Action, Budget
            from .executor import Executor
            from .ledger import Ledger
            from .registry import SkillRegistry
            skill = validate_package(args.directory)
            registry = SkillRegistry()
            if registry.install(Path(args.directory)) != skill.digest:
                raise ValueError('Package changed during validation')
            operation = next((op for op in skill.manifest.operations if op.id == args.operation),None)
            if operation is None:
                raise ValueError('Operation is not declared')
            with tempfile.TemporaryDirectory(prefix='sable-smoke-') as folder:
                ledger = Ledger(Path(folder)/'smoke.sqlite')
                ledger.create_campaign('smoke','contributor',Budget(max_actions=1,max_units=operation.budget_units))
                executor = Executor(registry,ledger,grants={'contributor':{(skill.manifest.id,skill.manifest.version,operation.id)}})
                action = Action(campaign_id='smoke',action_id='operation',skill_id=skill.manifest.id,
                                skill_version=skill.manifest.version,skill_digest=skill.digest,
                                operation_id=operation.id,inputs=json.loads(Path(args.inputs).read_text()))
                result = {'result':executor.execute(action,owner='contributor'),'events':ledger.events('smoke','contributor')}
        elif args.command == 'list':
            result = SkillStore(args.store).records()
        elif args.command == 'schema':
            result = Manifest.model_json_schema()
        else:
            result = SkillStore(args.store).set_state(args.id,args.version,args.command)
        print(json.dumps(result,indent=2))
        return 0
    except (ValueError,OSError,SyntaxError,RuntimeError) as exc:
        print(json.dumps({'error':str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
