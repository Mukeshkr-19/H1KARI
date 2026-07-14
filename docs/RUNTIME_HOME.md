# HIKARI Runtime Home

Status: WP-006 initialization slice

`HIKARI_HOME` is the private state root. It defaults to the user-private HIKARI
directory and must never be a code checkout. Specific database/path overrides keep
precedence as documented in `docs/RUNTIME_PATH_COMPATIBILITY.md`.

## Read-only plan

```bash
python hikari.py --init-plan --startup-mode text
python hikari.py --init-plan --startup-mode voice --voice-backend openai-whisper
```

The plan reports the exact directories, selected startup mode, possible first-use
model download, and audio-egress behavior. It creates nothing and loads no model.

## Initialize

```bash
python hikari.py --init --startup-mode text
python hikari.py --init --startup-mode voice --voice-backend faster-whisper
```

Initialization creates only missing private directories with owner-only permissions
and writes `runtime.json` with owner-only permissions. It never overwrites a config,
changes an existing directory's permissions, follows a brain symlink, imports a
model, or downloads weights. Repeating the exact command is a no-op; conflicting or
incomplete existing state fails closed.

The recorded voice choice is an initialization disclosure and migration input. The
current legacy voice entrypoints retain their documented backend chains until the
central voice-policy work enforces one runtime route. Use `--voice-status` to inspect
those current chains before enabling microphone modes.

## Backup

```bash
python hikari.py --runtime-backup
python hikari.py --runtime-backup --backup-destination /existing/private/parent/backup-name
```

Backup requires a valid initialized runtime home. It copies without following
symlinks and excludes the runtime `backups` directory to prevent recursion. The
default destination is a UTC-named directory under `HIKARI_HOME/backups`.

## Migration dry run

```bash
python hikari.py --migration-plan
```

The planner identifies an existing runtime brain directory, a legacy brain symlink,
or an optional sibling private brain. It does not enumerate private content and never
copies, moves, or removes anything. Migration apply remains an explicit future step
after backup and owner review.

## Roll back initialization

```bash
python hikari.py --rollback-init ROLLBACK
```

Rollback removes `runtime.json` and only the empty directories recorded as created
by initialization. It refuses unknown manifest paths, symlinks, missing directories,
and any created directory that now contains unrecognized data. Pre-existing paths
are never removed.
