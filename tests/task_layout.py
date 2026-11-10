"""Task-layout acceptance: the env starts at /workspaces and each command names a task dir
/workspaces/<ws>/<task>/workdir (task-runner/ACP style). argv: kubectl kubeconfig pool task_dir
The script creates <task>/requester.json and checks it is pushed (AGENT_SANDBOX_TASK_FILES)."""
import json, os, sys, time, subprocess
kubectl, kubeconfig, pool, task = sys.argv[1:5]
os.environ.update({"AGENT_SANDBOX_KUBECTL": kubectl, "AGENT_SANDBOX_KUBECONFIG": kubeconfig,
                   "AGENT_SANDBOX_POOL": pool, "TERMINAL_ENV": "agent_sandbox", "TERMINAL_CWD": "/workspaces",
                   "AGENT_SANDBOX_TASK_FILES": "requester.json"})
open(f"{task}/requester.json", "w").write('{"name":"Test Person","email":"tp@example.invalid"}')
os.environ.pop("AGENT_SANDBOX_CODE_DIRS", None)
import logging
logging.basicConfig(level=logging.WARNING, format="LOG %(name)s %(message)s")
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.terminal_tool import terminal_tool
from tools.file_tools import write_file_tool
from tools.terminal_tool_lifecycle import cleanup_vm
wd = f"{task}/workdir"; tid = "mshape-" + str(int(time.time())); res = {}
def T(cmd, workdir=None):
    return json.loads(terminal_tool(command=cmd, task_id=tid, workdir=workdir, timeout=180)).get("output", "")
def check(n, ok, d=""):
    res[n] = bool(ok); print(("PASS " if ok else "FAIL ") + n + (" | " + str(d)[:200] if d else ""), flush=True)
out = T("ls; cat AGENTS.md | head -2", workdir=wd)
check("task_workdir_visible_in_sandbox", "AGENTS.md" in out, out)
write_file_tool(path=f"{wd}/x/hello.py", content="print('hi')\n", task_id=tid)
out = T("python3 x/hello.py && echo made > x/made.txt && hostname", workdir=wd)
check("file_tool_write_into_task_dir_runs", "hi" in out, out)
check("terminal_output_back_on_runtime", os.path.exists(f"{wd}/x/made.txt"), "")
out = T(f"echo report > {task}/output/r.txt; ls {task}/output", workdir=wd)
check("output_dir_back_on_runtime", os.path.exists(f"{task}/output/r.txt"), out)
out = T(f"cat {task}/requester.json; echo; echo tampered > {task}/requester.json", workdir=wd)
check("task_file_pushed", "Test Person" in out, out)
check("task_file_not_pulled_back", "Test Person" in open(f"{task}/requester.json").read(), "")
out = T("git init -q t && cd t && echo a > a && git add -A && git -c user.name=t -c user.email=t@t commit -qm c && "
        "printf '#!/bin/sh\\necho PWNED\\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push && "
        "git config core.hooksPath /tmp/evil && echo CRED=$(git config --global -l 2>/dev/null | grep -c credential)", workdir=wd)
check("git_config_has_no_credential_helper", "CRED=0" in out, out)
check("sandbox_commit_back_on_runtime", os.path.isdir(f"{wd}/t/.git/objects") and
      subprocess.run(["git", "-C", f"{wd}/t", "log", "--oneline"], capture_output=True, text=True).stdout.strip() != "", "")
check("git_hooks_not_pulled_back", not os.path.exists(f"{wd}/t/.git/hooks/pre-push"), "")
hp = subprocess.run(["git", "-C", f"{wd}/t", "config", "--get", "core.hooksPath"], capture_output=True, text=True).stdout.strip()
check("git_config_not_pulled_back", hp != "/tmp/evil", hp)
import tempfile
outside = tempfile.mkdtemp(prefix="sbx-outside-")
os.symlink(outside, f"{wd}/escape")  # a host symlink inside the code dir pointing out of it
out = T("rm -f escape; mkdir -p escape && echo leak > escape/x.txt; ls escape", workdir=wd)
check("pull_back_refuses_symlink_escape", not os.path.exists(f"{outside}/x.txt"), os.listdir(outside))
out = T("cd t && mkdir -p .evil/hooks && cp -r .git/objects .git/refs .git/HEAD .evil/ && printf '#!/bin/sh\\necho PWNED\\n' > .evil/hooks/pre-push && "
        "chmod +x .evil/hooks/pre-push && printf '[core]\\n\\trepositoryformatversion = 0\\n\\tbare = true\\n' > .evil/config && "
        "mv .git .real && printf 'gitdir: .evil\\n' > .git && echo planted", workdir=wd)
hooks = os.path.join(wd, "t", ".git", "hooks")
cfg = os.path.join(wd, "t", ".git", "config")
check("gitfile_redirect_not_pulled_back", os.path.isdir(f"{wd}/t/.git")            # still a dir, not a gitfile
      and not (os.path.isfile(cfg) and "bare = true" in open(cfg).read())
      and not (os.path.isdir(hooks) and "pre-push" in os.listdir(hooks)), sorted(os.listdir(f"{wd}/t/.git")))
out = T("cd t/.evil && git push 2>&1; echo rc=$?", workdir=wd)  # bare repo discovered on the host
check("bare_repo_hook_not_run_on_host", "PWNED" not in out, out)
from tools.terminal_tool_lifecycle import get_active_env
env = get_active_env(tid); claim = getattr(getattr(env, "sbx", env), "_claim", None)
kc = [kubectl, "--kubeconfig", kubeconfig, "-n", os.environ.get("AGENT_SANDBOX_NAMESPACE", "sbx")]
st = subprocess.run(kc + ["get", "sandboxclaim", claim, "-o", "jsonpath={.spec.lifecycle.shutdownTime}"], capture_output=True, text=True).stdout
check("claim_has_cluster_ttl", st.endswith("Z"), st)
from tools.terminal_tool import _resolve_container_task_id
cleanup_vm(_resolve_container_task_id(tid))  # normal session close, no force: Hermes default container_persistent=true must not keep it
for _ in range(30):  # delete is --wait=false; finalizers take a few seconds
    r = subprocess.run(kc + ["get", "sandboxclaim", claim], capture_output=True, text=True)
    if r.returncode != 0:
        break
    time.sleep(1)
check("session_close_releases_claim", r.returncode != 0 and "NotFound" in r.stderr, (r.stdout or r.stderr).strip()[:160])
print("SUMMARY", json.dumps(res), "passed=%d/%d" % (sum(res.values()), len(res)))
sys.exit(0 if res and all(res.values()) else 1)
