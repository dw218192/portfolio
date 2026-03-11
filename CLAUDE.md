## Repo tooling

This project uses [repokit](tools/framework/README.md) for general project tooling (e.g. build, test, format).

- **CLI**: `./repo <command>` (or `repo.cmd` on Windows). Run `./repo --help` to discover commands.
- **Config**: `config.yaml` at the project root.
- **Framework path**: `tools/framework/`

### Contributing to the framework
1. `cd tools/framework && git fetch origin && git switch main && git pull --ff-only origin main`
2. Make changes, bump the version in `pyproject.toml`, add a `CHANGELOG.md` entry
3. Commit, push, and wait for CI to pass
4. Back in this project: `cd tools/framework && git checkout v<new-version>`
5. Commit the submodule pointer update

### Do not edit

These paths are generated or managed by the framework:

- `tools/framework/` — contribute upstream instead
- `tools/framework/_managed/` — generated venv, lockfile, pyproject
- `repo`, `repo.cmd`, `repo.ps1` — generated CLI shims
