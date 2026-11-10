# Fork conventions

Upstream: https://github.com/xiaoyaner0201/hermes-sandbox-cluster

- Keep one writable private repository; do not enable pull mirroring.
- `upstream/*` preserves source branches/tags. Never rewrite published upstream tags.
- `feature/*`, `hotfix/*`, `release/*` hold review candidates; `develop` is integration.
- `main` is the reviewed release baseline. A prerelease on `main` does not establish deployment
  acceptance; keep live-runtime validation and its private evidence separate.
- Require pull requests for `main`; source synchronization alone may write `upstream/*`.
- Deployment tags use `vYYYY-MM-DD.N`, immutable per repository. Public plugin version tags
  retain the project's existing `v<plugin-version>` scheme and do not imply deployment.
- Keep credentials, host paths, runtime data and private acceptance evidence outside this tree.
- Public changes must preserve source attribution/license and pass privacy scans before mirroring.
- An accepted push, a published release and a verified deployment are separate milestones.
