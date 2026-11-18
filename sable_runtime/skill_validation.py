"""Validate package instructions and entry points without executing package code."""
import ast
from pathlib import Path
import yaml
from .registry import SkillRegistry


def validate_package(directory):
    registry = SkillRegistry()
    directory = Path(directory)
    digest = registry.install(directory)
    skill, = registry._skills.values()
    lines = skill.instructions.splitlines()
    if not lines or lines[0] != '---' or '---' not in lines[1:]:
        raise ValueError('SKILL.md requires YAML frontmatter')
    end = lines[1:].index('---') + 1
    try:
        metadata = yaml.safe_load('\n'.join(lines[1:end]))
    except yaml.YAMLError:
        raise ValueError('Invalid instruction frontmatter') from None
    if not isinstance(metadata, dict) or metadata.get('name') != skill.manifest.id:
        raise ValueError('Instruction name must match the manifest id')
    if not isinstance(metadata.get('description'), str) or not metadata['description'].strip():
        raise ValueError('Instructions require a description')
    if not '\n'.join(lines[end+1:]).strip():
        raise ValueError('Instructions require a nonempty body')
    entries = [('operations.py', op.handler, 2) for op in skill.manifest.operations]
    entries += [(w.file, w.handler, 1) for w in skill.manifest.workers.values()]
    for job in skill.manifest.remote_jobs.values():
        entries += [(job.file, job.submit, 2), (job.file, job.poll, 3)]
        if job.cancel:
            entries.append((job.file, job.cancel, 3))
    if skill.manifest.requirements.probe:
        probe = skill.manifest.requirements.probe
        entries.append((probe.file, probe.handler, 0))
    if skill.manifest.completion_check:
        hook = skill.manifest.completion_check
        entries.append((hook.file, hook.handler, 2))
    trees = {name: ast.parse(code, filename=name) for name, code in skill.files.items() if name.endswith('.py')}
    unsupported_polls = {(job.file,job.poll) for job in skill.manifest.remote_jobs.values() if not job.reconcilable}
    for filename, handler, count in entries:
        definitions = [node for node in trees[filename].body if isinstance(node, ast.FunctionDef) and node.name == handler]
        if len(definitions) != 1:
            raise ValueError(f'{filename} must declare one synchronous top-level {handler} function')
        if (filename,handler) not in unsupported_polls and any(isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
               and isinstance(node.exc.func, ast.Name) and node.exc.func.id == 'NotImplementedError'
               for node in definitions[0].body):
            raise ValueError(f'{filename}:{handler} is an unfinished entry point')
        args = definitions[0].args
        positional = len(args.posonlyargs) + len(args.args)
        if (count < positional-len(args.defaults) or (count > positional and args.vararg is None)
                or any(default is None for default in args.kw_defaults)):
            raise ValueError(f'{filename}:{handler} does not accept {count} positional arguments')
    return skill
