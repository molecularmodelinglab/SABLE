"""Small executable examples for package contributors to adapt."""
import json
from pathlib import Path
from .contracts import Manifest


OBJECT = {'type':'object','additionalProperties':False,'properties':{'text':{'type':'string','maxLength':4096}},'required':['text']}
RESULT = {'type':'object','additionalProperties':False,'properties':{'characters':{'type':'integer','minimum':0}},'required':['characters']}


def create_package(name, output, template='inline'):
    manifest = {'id':name,'version':'1.0.0','description':'Count characters in supplied text.', 'operations':[]}
    files = {}
    instructions = 'Use `count` to count characters in text. This is a deterministic text utility, not a scientific measurement.\n'
    if template in {'inline','worker'}:
        manifest['operations'] = [{'id':'count','description':'Count characters in the supplied text.',
                                   'handler':'count','input_schema':OBJECT,'output_schema':RESULT}]
        if template == 'inline':
            files['operations.py'] = '"""Count characters without external dependencies."""\n\n\ndef count(inputs, context):\n    return {"characters": len(inputs["text"])}\n'
        else:
            manifest['workers'] = {'count':{'file':'worker.py','handler':'count','timeout_seconds':30}}
            files['operations.py'] = '"""Dispatch text counting to a bounded subprocess."""\n\n\ndef count(inputs, context):\n    return context.run_worker("count", inputs)\n'
            files['worker.py'] = '"""Count characters in an isolated process."""\n\n\ndef count(inputs):\n    return {"characters": len(inputs["text"])}\n'
    elif template == 'remote':
        manifest['description'] = 'Starter package for a configured remote text service.'
        receipt = {'type':'object','additionalProperties':False,'properties':{'kind':{'const':'remote_job'},'job_id':{'type':'string'},'status':{'type':'string'}},'required':['kind','job_id','status']}
        manifest['operations'] = [{'id':'count','description':'Queue a request to the configured service.',
                                   'handler':'count','input_schema':OBJECT,'output_schema':receipt}]
        manifest['remote_jobs'] = {'count':{'file':'remote.py','submit':'submit','poll':'poll',
                                          'service':name,'timeout_seconds':900,'poll_interval_seconds':5,'output_schema':RESULT}}
        manifest['requirements'] = {'services':[name]}
        files['operations.py'] = '"""Queue remote work before contacting the provider."""\n\n\ndef count(inputs, context):\n    return context.submit_job("count", inputs)\n'
        files['remote.py'] = '''"""Provider callbacks to implement against the intended service contract."""


def submit(payload, context):
    # Use context.service.endpoint and context.secret; never accept them from model input.
    # Make one bounded submission without automatic retries; return {"remote_id": ...}.
    raise NotImplementedError("Implement the provider submission before activating this package")


def poll(remote_id, payload, context):
    # Return running, succeeded/partial with a validated result, failed, or cancelled.
    # Only repeat-safe status/result retrieval belongs here.
    raise NotImplementedError("Implement provider polling before activating this package")
'''
        instructions = 'Starter only: implement and test the provider callbacks before activation. Queue remote work through `count`; a job receipt is not a measurement. Respect uncertainty and provider-specific cancellation support.\n'
    elif template == 'workflow':
        manifest['description'] = 'Guidance for composing installed capabilities.'
        instructions = 'Load the capabilities needed for the user\'s objective. Pass recorded action references between operations, report limitations, and cite their results. Add task-specific guidance and declare skill dependencies before publishing this workflow.\n'
    else:
        raise ValueError('Unknown template')
    Manifest.model_validate(manifest)
    path = Path(output)
    if path.exists():
        raise ValueError('Output already exists; templates never overwrite a package')
    path.mkdir(parents=True)
    files['manifest.json'] = json.dumps(manifest,indent=2)+'\n'
    files['SKILL.md'] = f'---\nname: {name}\ndescription: {manifest["description"]}\n---\n\n'+instructions
    for filename, content in files.items():
        (path/filename).write_text(content)
    return path
