"""Execute one trusted installation snapshot; no tool-specific dispatch."""
from contextlib import redirect_stdout
import json
import sys


def main():
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            namespace = {'__name__': 'sable_skill_worker'}
            exec(compile(request['code'], f"<skill:{request['file']}>", 'exec'), namespace)
            output = namespace[request['handler']](request['payload'])
        print(json.dumps(output, allow_nan=False))
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(main())
