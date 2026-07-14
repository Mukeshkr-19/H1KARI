# Runtime Path Compatibility

Status: WP-005 naming boundary

## Canonical names

- `HIKARI_REPO_ROOT` identifies the H1KARI code checkout used by installed CLI
  wrappers and the login agent.
- `HIKARI_HOME` is reserved for the private runtime-state root. WP-006 defines
  its layout, initialization, backup, and migration behavior.
- Specific test and operator overrides such as `HIKARI_BRAIN_DIR`,
  `HIKARI_BRAIN_V2_EPISODES_DB`, `HIKARI_NEURAL_MEMORY_DB`,
  `HIKARI_LEGACY_DATA_DIR`, and `HIKARI_TASKS_DB` retain precedence over future
  defaults derived from `HIKARI_HOME`.

## Compatibility rule

Wrappers installed before WP-005 exported `HIKARI_HOME=<checkout>`. The launcher
accepts that legacy meaning only when the directory contains `hikari.py`. It then
exports the same path as `HIKARI_REPO_ROOT` and removes the legacy `HIKARI_HOME`
value before starting Python, so runtime state cannot be written into the repo.

A `HIKARI_HOME` value that is not a recognizable HIKARI checkout is preserved as
runtime configuration and never used to locate code. New installers write only
`HIKARI_REPO_ROOT` for checkout discovery.

## Removal policy

The legacy repo-path interpretation remains through the WP-006 migration window.
It can be removed only after installed wrappers have an explicit upgrade path and
the removal is announced in release notes. No runtime path may fall back to the
public repository.
