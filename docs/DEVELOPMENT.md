# Development setup

Tools and tasks live in `mise.toml` and run through [mise](https://mise.jdx.dev). One command sets
up a machine, and it is safe to re-run:

```sh
python3 setup.py
```

`setup.py` needs only Python 3. It runs these steps and stops at the first failure, naming it:

1. Installs mise with the official installer from https://mise.run when `mise` is not on `PATH`
   or at `~/.local/bin/mise` ([getting started](https://mise.jdx.dev/getting-started.html)).
2. `mise trust` approves this repo's `mise.toml`.
3. `mise install` installs the pinned tools: Python, uv, Node.js (for `npx aws-cdk`), AWS CLI v2,
   1Password CLI (`op`), GitHub CLI, kubectl, Helm, jq, kind and Tilt.
4. `mise run init` installs the Python dependencies (`uv sync --locked`) and git hooks.

## Tasks

`mise tasks ls` lists them. Pass arguments after `--`, and select a deploy environment with `ENV`:

```sh
mise run test
ENV=sandbox.yaml mise run deploy -- --require-approval never
```

## Activate mise in your shell

Activation puts the pinned tools on `PATH` whenever you `cd` into the repo. `setup.py` prints the
line for your shell when mise is not on `PATH` yet:

```sh
echo 'eval "$(~/.local/bin/mise activate zsh)"' >> ~/.zshrc    # bash: activate bash, ~/.bashrc
```

Without activation, run tools through mise, for example `mise exec -- kubectl version`.

## 1Password CLI

`mise run secrets:github` reads the GitHub App from the `Prismatic` vault with `op read`. Turn on
the desktop app integration once, so `op` uses the app's unlock instead of a separate sign-in
([1Password docs](https://www.1password.dev/cli/app-integration)):

1. Install, open and unlock the 1Password desktop app, and select the account at the top of the
   sidebar.
2. Turn on system unlock: Touch ID (macOS), Windows Hello (Windows), or **Settings > Security >
   Unlock using system authentication** (Linux, needs PolKit and an authentication agent).
3. Open **Settings > Developer** and select **Integrate with 1Password CLI**.
4. Check it with `op vault list`; `Prismatic` must be listed. With several accounts, choose one
   with `op signin` or pass `--account`.

## Not managed by mise

- Docker (Docker Desktop, OrbStack or Colima): needed for `mise run test`, the chart checks and kind.
- AWS Session Manager plugin, for `mise run kube:connect`: `brew install --cask session-manager-plugin`.

## Updating a tool

Edit the version in `mise.toml` and run `mise install`. Keep Helm, kubectl and Node.js in line with
the versions pinned in `.github/workflows/`. The GitHub CLI uses the `github:cli/cli` backend
because the aqua registry expects release attestations that `gh` releases do not publish.
