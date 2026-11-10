# Repository guide

This repository is a thin Hermes terminal-environment adapter. The framework-independent SDK is
vendored unchanged from `xiaoyaner0201/self-hosted-agent-sandbox`; see
`plugin/agent_sandbox_backend/_vendor/VENDORED.md` for immutable provenance.

Maintenance policy: [`.maintainers/CONVENTIONS.md`](.maintainers/CONVENTIONS.md).

## Change boundaries

- Keep Hermes cwd/snapshot/stdin/timeout handling in `BaseEnvironment` via the adapter callback.
- Never patch the vendored SDK locally. Refresh both SDK files with `scripts/vendor-core.sh`.
- Keep dangerous-command approval on. Do not broaden host-command routing or credential forwarding.
- Strict artifact receipt is opt-in. Legacy cleanup is not verified resource reclamation.
- Do not install into an operator's active Hermes home or restart a live gateway to test this repo.
- Real cluster acceptance requires an explicitly selected disposable task directory and namespace.

## Verification

- `python3 tests/vendored_core.py` checks both SDK hashes and version offline.
- Set `SHAS_SOURCE_CHECKOUT` to compare the files against the recorded source commit too.
- `HERMES_SRC=<checkout> SHAS_TEST_IMAGE=<local-image> python tests/adapter_contract.py`
  exercises the real adapter and Hermes BaseEnvironment using isolated, networkless Docker
  containers. Use a Hermes-compatible interpreter. Kubernetes claims/deletes are synthetic here.
- `tests/accept.py` and `tests/task_layout.py` are separate real-cluster acceptance programs;
  inspect their docstrings before running. Their result must not be inferred from Docker tests.
- Scan the complete unpublished commit range and release archive for secrets/private paths.

## Candidate scope

Update the vendored SHAS SDK to the recorded 0.4.0rc1 source, repair the vendor refresh path for
its renamed Python package, and test callback receipt/recovery, cwd/snapshot/stdin, isolation,
host routing and legacy cleanup. Do not change production configuration, adopt verified release
by default, expand RBAC, or claim cluster/live-task acceptance before it has happened.
