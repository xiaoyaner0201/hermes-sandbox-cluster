"""End-to-end acceptance for the agent_sandbox backend, run on the Hermes host with the real Hermes.

Point HERMES_HOME at a scratch home whose config enables the plugin; the live gateway is untouched.
argv: kubectl kubeconfig pool code_dir conf_file
Optional env:
  SBX_TEST_MAVEN_GAV   group:artifact:version that must resolve through your Maven mirror
                       (skips the Maven checks when unset)
  SBX_TEST_SECRET_FILE a host-only secret path that must NOT be readable in the sandbox
"""
import json, os, sys, time, subprocess
kubectl, kubeconfig, pool, code, conf = sys.argv[1:6]
os.environ.update({"AGENT_SANDBOX_KUBECTL": kubectl, "AGENT_SANDBOX_KUBECONFIG": kubeconfig,
                   "AGENT_SANDBOX_POOL": pool, "TERMINAL_ENV": "agent_sandbox",
                   "AGENT_SANDBOX_CODE_DIRS": code, "AGENT_SANDBOX_SYNC_PATHS": conf,
                   "TERMINAL_CWD": code, "SBX_PROBE_VAR": "v1", "SBX_PROBE_VAR2": "x",
                   # allowlist: only these reach the sandbox; the fake API key below must not
                   "AGENT_SANDBOX_FORWARD_ENV": "SBX_PROBE_VAR,SBX_PROBE_VAR2,JAVA_TOOL_OPTIONS",
                   "SBX_PROBE_API_KEY": "sk-host-only",
                   # a listed host sub-command; "git version" is harmless and present everywhere
                   "AGENT_SANDBOX_HOST_COMMANDS": "git push,git fetch,git clone,git ls-remote,git version,uname -n"})
import logging
logging.basicConfig(level=logging.WARNING, format="LOG %(name)s %(message)s")
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.terminal_tool import terminal_tool
from tools.file_tools import write_file_tool, read_file_tool
from tools.terminal_tool_lifecycle import cleanup_vm

tid = "accept-" + str(int(time.time()))
res = {}
def T(cmd, timeout=180):
    t = time.time()
    r = json.loads(terminal_tool(command=cmd, task_id=tid, timeout=timeout))
    return r.get("output", r), r.get("exit_code", r.get("returncode")), round(time.time() - t, 2)

def check(name, ok, detail=""):
    res[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail)[:300] if detail else ""), flush=True)

out, rc, s = T("cat /proc/version; hostname; id -un; pwd")
check("runs_in_gvisor_sandbox", "gvisor" in out.lower() and os.uname().nodename not in out, f"{s}s {out!r}")

# 0. dangerous-command approval is not skipped for this backend (results reach the host)
try:
    from tools.approval import _should_skip_container_guards
    check("approval_guards_on", _should_skip_container_guards("agent_sandbox") is False, "")
except ImportError:
    from agent.terminal_env_registry import provider_flag
    check("approval_guards_on", provider_flag("agent_sandbox", "skip_container_guards", True) is False, "")

# 1. env allowlist + change visible next command
out, *_ = T("echo $SBX_PROBE_VAR; echo ${SBX_PROBE_VAR2:+has_probe2}; echo HOME=$HOME; echo JTO=$JAVA_TOOL_OPTIONS; echo key=${SBX_PROBE_API_KEY:-none}")
check("env_forwarded", "v1" in out and "has_probe2" in out, out)
check("env_unlisted_api_key_stays_on_host", "key=none" in out and "sk-host-only" not in out, out)
check("env_skip_home_and_java", "HOME=/home/agent" in out and "MaxRAMPercentage=50.0" in out, out)
os.environ["SBX_PROBE_VAR"] = "v2"
out, *_ = T("echo $SBX_PROBE_VAR")
check("env_change_next_command", out.strip().endswith("v2"), out)
_names = set(T("compgen -e | grep '^SBX_' | sort")[0].split())
check("env_only_allowlisted_probes", _names == {"SBX_PROBE_VAR", "SBX_PROBE_VAR2"}, sorted(_names))

# 2. declared config file: hot change visible next command, never copied back
open(conf, "w").write("value=one\n")
out, *_ = T(f"cat {conf}")
check("conf_pushed", "value=one" in out, out)
time.sleep(1.1); open(conf, "w").write("value=two-changed\n")
out, *_ = T(f"cat {conf}")
check("conf_hot_change", "value=two-changed" in out, out)
T(f"echo sandbox-edit > {conf}")
check("conf_not_copied_back", open(conf).read() == "value=two-changed\n", open(conf).read())

# 3. code round trip (file tool + terminal), deletions mirrored
write_file_tool(path=f"{code}/calc.py", content="def add(a, b):\n    return a + b\n", task_id=tid)
check("file_tool_write_on_runtime_tree", os.path.exists(f"{code}/calc.py"), "")
T("pip install -q pytest 2>&1 | tail -1", timeout=300)
out, *_ = T("printf 'from calc import add\\n\\ndef test_add():\\n    assert add(2, 3) == 5\\n' > test_calc.py; python3 -m pytest -q 2>&1 | tail -2")
check("tests_run_in_sandbox", "1 passed" in out, out)
check("terminal_created_file_pulled_back", os.path.exists(f"{code}/test_calc.py"), "")
open(f"{code}/runtime_only.txt", "w").write("from runtime\n")
out, *_ = T("cat runtime_only.txt; rm -f test_calc.py")
check("runtime_edit_seen_in_sandbox", "from runtime" in out, out)
check("sandbox_delete_mirrored", not os.path.exists(f"{code}/test_calc.py"), "")
out = read_file_tool(path=f"{code}/calc.py", task_id=tid)
check("file_tool_read", "return a + b" in out, out[:120])

# 4. git: commit in sandbox, host-side git command sees it
T("git init -q 2>/dev/null; git add -A && git -c user.email=t@t -c user.name=t commit -qm sbx-commit && git log --oneline | head -1")
# "uname -n" is listed as a host command for this test: on the host it prints the host's name,
# inside the sandbox it is a stub, so the host's name in the output means "ran on the host"
HOST = os.uname().nodename
out, *_ = T("git version >/dev/null 2>&1; uname -n")
check("host_only_command_runs_on_runtime", HOST in out, out)
out, *_ = T("cd . && echo hi && git ls-remote --help >/dev/null 2>&1; uname -n")
check("host_command_with_glue_on_runtime", "hi" in out and HOST in out, out)
# a real build step (bash -c ...) in the same command keeps it in the sandbox, where git network ops are stubs
out, *_ = T("bash -c 'exit 0'; git ls-remote https://example.invalid/x.git; echo rc=$?; uname -n")
check("mixed_build_and_host_stays_in_sandbox", "rc=97" in out and HOST not in out, out)
# host commands are confined: no path outside the code dir, no env expansion, no trace file, no file options
for probe in ("git ls-remote /tmp", "echo $HOME; git version", "GIT_TRACE=/tmp/sbx-trace git version",
              "cd / && git version", "git version | sort -o/tmp/sbx-sorted"):
    out, *_ = T(f"{probe}; uname -n")
    check("confined:" + probe[:28], HOST not in out, out)
lg = subprocess.run(["git", "-C", code, "log", "--oneline"], capture_output=True, text=True).stdout
check("sandbox_commit_visible_on_runtime", "sbx-commit" in lg, lg)
secret = os.environ.get("SBX_TEST_SECRET_FILE")
if secret:
    out, *_ = T(f"test -r {secret} && echo SECRET_READABLE || echo no-secret")
    check("host_secret_not_in_sandbox", "no-secret" in out, out)

gav = os.environ.get("SBX_TEST_MAVEN_GAV")
if gav:
    g, a, v = gav.split(":")
    pom = (f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
           f'<groupId>t</groupId><artifactId>t</artifactId><version>1</version><dependencies><dependency>'
           f'<groupId>{g}</groupId><artifactId>{a}</artifactId><version>{v}</version></dependency></dependencies></project>')
    os.makedirs(f"{code}/mvn", exist_ok=True); open(f"{code}/mvn/pom.xml", "w").write(pom)
    from tools.terminal_tool import _active_environments
    _env = next(v for k, v in _active_environments.items() if k in (tid, "default") or tid in k)
    def TL(cmd, timeout):
        t = time.time(); r = _env.execute(cmd, cwd=code, timeout=timeout)
        return r.get("output", ""), r.get("returncode"), round(time.time() - t, 2)
    out, rc, s = TL("cd mvn && mvn -B -q dependency:resolve -DexcludeTransitive=true > /tmp/mvn.log 2>&1; echo mvnrc=$?", 900)
    check("maven_artifact_resolves", "mvnrc=0" in out, f"{s}s {out}")
    out2, rc2, s2 = TL("cd mvn && mvn -B -q -o dependency:resolve -DexcludeTransitive=true > /tmp/mvn-off.log 2>&1; echo mvnrc=$?", 300)
    check("maven_cache_offline_hit", "mvnrc=0" in out2, f"{s2}s {out2}")

cleanup_vm(tid, force_remove=True)
print("SUMMARY", json.dumps(res), "passed=%d/%d" % (sum(res.values()), len(res)))
sys.exit(0 if res and all(res.values()) else 1)
