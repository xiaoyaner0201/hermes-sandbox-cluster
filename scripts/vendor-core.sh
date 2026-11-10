#!/usr/bin/env bash
# Refresh the unmodified SHAS SDK, resolving the source ref to an immutable commit.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - "$@" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen

if len(sys.argv) not in (2, 3):
    sys.exit('usage: scripts/vendor-core.sh <tag-or-commit> [local-SHAS-checkout]')
ref = sys.argv[1]
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', ref):
    sys.exit('expected a tag or commit, not a path or URL')
repo = 'xiaoyaner0201/self-hosted-agent-sandbox'
if len(sys.argv) == 3:
    checkout = sys.argv[2]
    def git(*args):
        return subprocess.check_output(['git', '-C', checkout, *args])
    commit = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    def read(path):
        return git('show', commit + ':' + path)
else:
    with urlopen(f'https://api.github.com/repos/{repo}/commits/{ref}', timeout=60) as r:
        commit = json.load(r)['sha']
    def read(path):
        with urlopen(f'https://raw.githubusercontent.com/{repo}/{commit}/{path}', timeout=60) as r:
            return r.read()
if not re.fullmatch(r'[0-9a-f]{40}', commit):
    sys.exit('source did not resolve to an immutable commit')
version = re.search(rb'^version = "([^"]+)"', read('pyproject.toml'), re.M).group(1).decode()
source = 'src/self_hosted_agent_sandbox'
files = {name: read(source + '/' + name) for name in ('core.py', '__init__.py')}
for name, data in files.items():
    compile(data, name, 'exec')
manifest = ('# Vendored: self-hosted-agent-sandbox SDK\n\n'
            f'Unmodified `{source}/core.py` and `__init__.py` from\n'
            f'https://github.com/{repo}, distributed under the repository Apache-2.0 LICENSE.\n'
            'The local `agent_sandbox` directory name preserves the adapter import path.\n'
            'Refresh: `scripts/vendor-core.sh <tag-or-commit> [local-SHAS-checkout]`.\n'
            'Verify offline: `python3 tests/vendored_core.py`.\n\n'
            f'- source ref: {ref}\n- source commit: {commit}\n- SDK version: {version}\n')
for name, data in files.items():
    blob = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
    manifest += f'- {name} git blob sha1: {blob}\n- {name} sha256: {hashlib.sha256(data).hexdigest()}\n'
dst = Path('plugin/agent_sandbox_backend/_vendor')
for name, data in files.items():
    (dst / 'agent_sandbox' / name).write_bytes(data)
(dst / 'VENDORED.md').write_text(manifest)
print(f'vendored SHAS {version} from {commit}')
PY
