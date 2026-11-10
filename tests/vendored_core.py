"""Check both vendored SDK files against the immutable provenance manifest. Offline.
Run: python3 tests/vendored_core.py
Optional: SHAS_SOURCE_CHECKOUT verifies bytes directly against the recorded git commit.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess

here = Path(__file__).resolve().parents[1] / 'plugin/agent_sandbox_backend/_vendor'
manifest = (here / 'VENDORED.md').read_text()
commit = re.search(r'^- source commit: ([0-9a-f]{40})$', manifest, re.M).group(1)
version = re.search(r'^- SDK version: (\S+)$', manifest, re.M).group(1)
for name in ('core.py', '__init__.py'):
    data = (here / 'agent_sandbox' / name).read_bytes()
    for label, actual in (
        ('git blob sha1', hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()),
        ('sha256', hashlib.sha256(data).hexdigest()),
    ):
        want = re.search(r'^- ' + re.escape(name + ' ' + label) + r': ([0-9a-f]+)$', manifest, re.M).group(1)
        assert actual == want, f'{name} {label} mismatch'
    assert b'from tools.' not in data and b'hermes_' not in data, 'SDK must not import Hermes'
    if os.environ.get('SHAS_SOURCE_CHECKOUT'):
        upstream = subprocess.check_output(['git', '-C', os.environ['SHAS_SOURCE_CHECKOUT'],
                                           'show', commit + ':src/self_hosted_agent_sandbox/' + name])
        assert data == upstream, f'{name} differs from source commit'
assert f'__version__ = "{version}"' in (here / 'agent_sandbox/__init__.py').read_text()
print('vendored SDK ok', version, commit)
