"""Real Hermes callback + real Docker shell/tar; synthetic Kubernetes control plane only.

Run with a Hermes-compatible interpreter (no plugin installation):
  HERMES_SRC=/path/to/hermes SHAS_TEST_IMAGE=<already-local-image> python tests/adapter_contract.py
Docker must already contain a Linux image with bash, python3, git, tar and sha256sum.
No network, host credentials, real kubeconfig, live gateway or Kubernetes resource is used.
"""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get('HERMES_SRC') or not os.environ.get('SHAS_TEST_IMAGE'):
    sys.exit('HERMES_SRC and SHAS_TEST_IMAGE (already local, no pulls) are required')
sys.path.insert(0, os.environ['HERMES_SRC'])
sys.path.insert(0, str(ROOT / 'plugin'))
# Isolate HOME before importing Hermes. Never discover/load the installed plugins.
HOME = tempfile.TemporaryDirectory(prefix='adapter-home-')
os.environ['HERMES_HOME'] = HOME.name
os.environ['HOME'] = HOME.name
for key in list(os.environ):
    if key.startswith('AGENT_SANDBOX_'):
        os.environ.pop(key)
from tools.environments.base import BaseEnvironment
from agent_sandbox_backend.environment import AgentSandboxEnvironment
from agent_sandbox_backend.provider import AgentSandboxProvider
from agent_sandbox_backend._vendor.agent_sandbox import Allocation, ReleaseError, Sandbox
from agent_sandbox_backend._vendor.agent_sandbox import core


def docker(*args, check=True):
    return subprocess.run(['docker', *args], capture_output=True, timeout=60, check=check)


class AdapterContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.containers = []
        image = os.environ['SHAS_TEST_IMAGE']
        docker('image', 'inspect', image)
        try:
            for _ in range(2):
                name = 'hermes-adapter-' + uuid.uuid4().hex[:12]
                temp_root = '/' + Path(tempfile.gettempdir()).resolve().parts[1]
                mounts = [] if temp_root == '/tmp' else [
                    '--tmpfs', temp_root + ':rw,nosuid,nodev,size=128m,uid=10001,gid=10001']
                docker('run', '--pull=never', '-d', '--name', name, '--network', 'none',
                       '--read-only', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
                       '--memory', '512m', '--pids-limit', '128', '--user', '10001:10001',
                       '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,uid=10001,gid=10001',
                       *mounts, image, 'sleep', 'infinity')
                cls.containers.append(name)
        except BaseException:
            cls.tearDownClass()
            raise
        print('REAL Hermes BaseEnvironment + Docker shell/tar; FAKE claim/delete; NOT k3s/gVisor', flush=True)

    @classmethod
    def tearDownClass(cls):
        for name in cls.containers:
            docker('rm', '-f', name)
            assert docker('inspect', name, check=False).returncode != 0
        print('test containers removed and absence verified', flush=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='adapter-contract-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.fault = self.root / 'fault'
        self.envs = []
        self.deletes = []
        self.transport = self.root / 'transport'
        self.transport.write_text('''#!PYTHON
import io, pathlib, subprocess, sys, tarfile
fault = pathlib.Path(FAULT)
a = sys.argv[1:]
mode = fault.read_text() if fault.exists() else ''
pull = a[1:3] == ['bash', '-c'] and 'changed.tar' in a[3]
r = subprocess.run(['docker', 'exec', '-i', *a], input=sys.stdin.buffer.read(), capture_output=True)
out = r.stdout
if pull and mode == 'truncate': out = out[:4096]
sys.stdout.buffer.write(out)
sys.stderr.buffer.write(r.stderr)
sys.exit(r.returncode)
'''.replace('PYTHON', sys.executable).replace('FAULT', repr(str(self.fault))))
        self.transport.chmod(0o755)
        self.addCleanup(self.cleanup_envs)

    def cleanup_envs(self):
        self.fault.unlink(missing_ok=True)
        for env in self.envs:
            env.cleanup(force_remove=True)

    def make_env(self, *, index=0, strict=False, persistent=False):
        work = self.root / ('task-' + str(index))
        work.mkdir(exist_ok=True)
        (work / 'child').mkdir(exist_ok=True)
        (work / 'child/seed').write_text('seed')
        container = self.containers[index]
        def acquire(sb):
            sb._claim, sb._pod = 'synthetic-' + sb._task_label, container
            sb._exec_argv = argv.__get__(sb)
            sb._run_k = control.__get__(sb)
            sb._renew_ttl = lambda: None
            sb._observe_allocation(Allocation(sb._ns, sb._claim, 'uid-' + sb._task_label))
        def argv(sb, *args, **kwargs):
            return [str(self.transport), sb._pod, *args]
        def control(sb, *args, **kwargs):
            self.deletes.append((sb._ns, args))
            return subprocess.CompletedProcess(args, 0, '', '')
        # Transport/lifecycle only are replaced; SDK initialization, sync and callback execute are real.
        p = patch.object(Sandbox, '_acquire', acquire)
        p.start()
        self.addCleanup(p.stop)
        config = dict(agent_sandbox_kubectl='/nonexistent-offline-fixture',
                      agent_sandbox_kubeconfig=str(self.root / 'no-kubeconfig'),
                      agent_sandbox_code_dirs=[str(work)], agent_sandbox_pool='pool-' + str(index),
                      agent_sandbox_namespace='tenant-' + str(index), agent_sandbox_strict_pull=strict,
                      agent_sandbox_persistent=persistent, agent_sandbox_forward_env='BUILD_FLAG',
                      agent_sandbox_host_commands='git version')
        env = AgentSandboxEnvironment(cwd=str(work), timeout=15, task_id='session-' + str(index), container_config=config)
        self.envs.append(env)
        self.assertTrue(env._snapshot_ready)
        return env, work

    def test_snapshot_cwd_env_stdin_and_nonzero(self):
        with patch.dict(os.environ, {'BUILD_FLAG': 'first', 'UNLISTED_SECRET': 'synthetic-private'}):
            env, work = self.make_env()
            r = env.execute('export SESSION_VAR=kept; cd child; printf "%s" "$BUILD_FLAG"')
            self.assertEqual(r['returncode'], 0)
            self.assertEqual(env.cwd, str(work / 'child'))
            os.environ['BUILD_FLAG'] = 'second'
            r = env.execute('printf "%s:%s:%s" "$BUILD_FLAG" "$SESSION_VAR" "${UNLISTED_SECRET:-absent}"')
            self.assertIn('second:kept:absent', r['output'])
            r = env.execute('python3 -c "import sys;print(sys.stdin.read())"', stdin_data='payload\n')
            self.assertIn('payload', r['output'])
            r = env.execute('printf failed; false')
            self.assertEqual(r['returncode'], 1)
            self.assertIn('failed', r['output'])

    def test_roundtrip_binary_and_delete(self):
        env, work = self.make_env()
        (work / 'old').write_text('old')
        r = env.execute('rm old; python3 -c "from pathlib import Path;Path(\'artifact\').write_bytes(bytes(range(256))*4096)"')
        self.assertEqual(r['returncode'], 0)
        self.assertFalse((work / 'old').exists())
        self.assertEqual(hashlib.sha256((work / 'artifact').read_bytes()).hexdigest(),
                         hashlib.sha256(bytes(range(256))*4096).hexdigest())

    def test_strict_receipt_failure_blocks_stale_push_and_no_rerun(self):
        env, work = self.make_env(strict=True)
        self.fault.write_text('truncate')
        with self.assertRaisesRegex(RuntimeError, 'command may have completed'):
            env.execute('printf once >> runs; printf remote > result')
        self.assertTrue(env.sbx._pull_pending)
        with self.assertRaises((RuntimeError, tarfile.ReadError)):
            env.execute('printf must-not-run >> runs')
        self.fault.unlink()
        env.sbx.pull()
        self.assertEqual((work / 'runs').read_text(), 'once')
        self.assertEqual((work / 'result').read_text(), 'remote')
        self.assertFalse(env.sbx._pull_pending)
        self.assertEqual(env.execute('printf next')['returncode'], 0)

    def test_legacy_best_effort_still_recovers_before_next_push(self):
        env, work = self.make_env()
        self.assertFalse(env.sbx._strict_pull)
        self.fault.write_text('truncate')
        self.assertEqual(env.execute('printf preserved > result')['returncode'], 0)
        self.assertTrue(env.sbx._pull_pending)
        with self.assertRaises((RuntimeError, tarfile.ReadError)):
            env.execute('printf unexpected > result')
        self.fault.unlink()
        env.execute('printf ok')
        self.assertEqual((work / 'result').read_text(), 'preserved')

    def test_callback_exception_retains_pending(self):
        env, work = self.make_env()
        run = env._run_bash
        def failing(*args, **kwargs):
            p = run(*args, **kwargs)
            p.communicate(timeout=15)
            raise OSError('synthetic transport failure after execution')
        with patch.object(env, '_run_bash', failing):
            with self.assertRaisesRegex(OSError, 'after execution'):
                env.execute('printf once >> runs')
        self.assertTrue(env.sbx._pull_pending)
        env.sbx.pull()
        self.assertEqual((work / 'runs').read_text(), 'once')

    def test_timeout_metadata_preserved(self):
        env, _ = self.make_env()
        r = env.execute('sleep 3', timeout=1)
        self.assertEqual(r['returncode'], 124)
        # Newer Hermes annotates its own deadline; older supported hosts need not.
        if 'hermes_timed_out' in r:
            self.assertTrue(r['hermes_timed_out'])

    def test_multi_pool_namespace_session_isolation(self):
        a, aw = self.make_env(index=0)
        b, bw = self.make_env(index=1)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(a.execute, 'export SESSION_VAR=alpha; printf alpha > result')
            two = pool.submit(b.execute, 'printf "%s" "${SESSION_VAR:-clean}"; printf beta > result')
            self.assertEqual(one.result()['returncode'], 0)
            r = two.result()
        self.assertIn('clean', r['output'])
        self.assertEqual((aw / 'result').read_text(), 'alpha')
        self.assertEqual((bw / 'result').read_text(), 'beta')
        self.assertNotEqual(a._snapshot_path, b._snapshot_path)
        self.assertNotEqual(a.sbx._pool, b.sbx._pool)
        self.assertNotEqual(a.sbx.allocation.namespace, b.sbx.allocation.namespace)
        self.assertNotEqual(a.sbx._claim, b.sbx._claim)

    def test_host_routing_and_approval_unchanged(self):
        env, work = self.make_env()
        with patch.object(BaseEnvironment, 'execute', side_effect=AssertionError('host command must not use sandbox callback')):
            r = env.execute('git version')
        self.assertIn('git version', r['output'])
        for command in ['git version; bash -c true', 'cd / && git version', 'GIT_TRACE=/tmp/trace git version']:
            self.assertFalse(env.sbx._is_host_command(command, str(work)))
        self.assertFalse(AgentSandboxProvider().skip_container_guards)
        self.assertFalse(AgentSandboxProvider().uses_host_filesystem if hasattr(AgentSandboxProvider(), 'uses_host_filesystem') else False)

    def test_cleanup_retains_legacy_contract_and_allocation(self):
        env, _ = self.make_env(persistent=True)
        allocation = env.sbx.allocation
        with patch.object(env.sbx, 'release_verified', side_effect=AssertionError('must remain opt-in')):
            env.cleanup()
        self.assertEqual(self.deletes, [])
        self.assertIsNone(env.sbx._claim)
        self.assertEqual(env.sbx.allocation, allocation)
        self.assertTrue(issubclass(ReleaseError, RuntimeError))


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        HOME.cleanup()
