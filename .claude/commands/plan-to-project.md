Convert a markdown requirements plan into a fully structured GitHub Project backlog.

## Required Arguments

You MUST ask the user for these values before proceeding if not provided:

- `PLAN_FILE` — path to the markdown plan file
- `ORG` — GitHub organization login (e.g., `kdtix-open`)
- `REPO` — GitHub repo in owner/name format (e.g., `kdtix-open/my-project`)
- `PROJECT_NUMBER` — GitHub Project V2 number (integer)

## Prerequisites Check

Before starting, verify:
1. `gh auth status` succeeds (if not, tell the user to run `gh auth login`)
2. Python 3.9+ is available (`python3 --version`)
3. The plan file exists at the given path

## Workflow

Execute each phase in order. Stop and report errors if any phase fails.

### Phase 1 — Preflight validation

```bash
python scripts/create_issues.py preflight --org $ORG --repo $REPO --project $PROJECT_NUMBER
```

This validates that the GitHub org has the required Issue Types (Project Scope, Initiative, Epic, User Story, Task) and the Project V2 has required fields (Priority, Size, Status). Writes `manifest-config.json`.

### Phase 2 — Parse plan (dry run)

```bash
python scripts/create_issues.py parse --plan $PLAN_FILE
```

Show the user the hierarchy summary (counts of scope, initiatives, epics, stories, tasks) and ask for confirmation before creating issues.

### Phase 3 — Create all issues

```bash
python scripts/create_issues.py create --plan $PLAN_FILE --org $ORG --repo $REPO --project $PROJECT_NUMBER
```

Creates all issues top-down. Writes `manifest.json` with issue IDs.

### Phase 4 — Set sub-issue relationships and blocking labels

```bash
python scripts/set_relationships.py --manifest manifest.json --repo $REPO
```

Links child issues to parents and applies `blocks`/`blocked` labels.

### Phase 5 — Set project fields and Issue Types

```bash
python scripts/set_project_fields.py --manifest manifest.json --config manifest-config.json --org $ORG --project $PROJECT_NUMBER
```

Sets Priority, Size, Status, and Issue Type on every issue.

### Phase 6 — Compliance check

```bash
python scripts/compliance_check.py --manifest manifest.json --repo $REPO
```

Checks issue bodies for template compliance. Auto-fixes P0 gaps. Reports P1/P2 gaps.

### Phase 7 — Queue order

```bash
python scripts/queue_order.py --manifest manifest.json --repo $REPO --project $PROJECT_NUMBER
```

Outputs recommended story execution order sorted by priority, size, and issue number.

## After completion

Report to the user:
- Total issues created (from manifest.json)
- Compliance report summary (P0 fixed, P1/P2 gaps from compliance-report.json)
- Recommended queue order (from queue-order.json)
- Link to the GitHub Project board
