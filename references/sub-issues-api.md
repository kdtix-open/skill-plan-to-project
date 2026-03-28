# Sub-Issues REST API Reference

GitHub's sub-issues API links a child issue under a parent issue.

## Add Sub-Issue

```
POST /repos/{owner}/{repo}/issues/{issue_number}/sub_issues
```

**Required body field:** `sub_issue_id` — the **integer** `databaseId` of the child issue (NOT the `node_id`).

### gh CLI invocation:

```bash
gh api \
  --method POST \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/{owner}/{repo}/issues/{parent_number}/sub_issues \
  -F sub_issue_id={child_database_id}
```

> **Critical:** Use `-F` (not `-f`) for integer fields. `-f` sends strings; `-F` sends typed values. The API rejects string IDs.

### Python subprocess pattern:

```python
import subprocess, sys

def add_sub_issue(repo: str, parent_number: int, child_db_id: int) -> None:
    result = subprocess.run(
        [
            "gh", "api",
            "--method", "POST",
            "-H", "Accept: application/vnd.github+json",
            "-H", "X-GitHub-Api-Version: 2022-11-28",
            f"/repos/{repo}/issues/{parent_number}/sub_issues",
            "-F", f"sub_issue_id={child_db_id}",
        ],
        text=True, encoding="utf-8",
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] sub-issue link failed: {result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)
```

## Get Sub-Issues for an Issue

```bash
gh api /repos/{owner}/{repo}/issues/{issue_number}/sub_issues
```

## Remove Sub-Issue

```
DELETE /repos/{owner}/{repo}/issues/{issue_number}/sub_issues
```

Body: `{ "sub_issue_id": 123456 }`

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `422 Unprocessable Entity` | `sub_issue_id` sent as string | Use `-F` not `-f` |
| `404 Not Found` | Wrong endpoint or repo | Verify `/repos/owner/repo/issues/N/sub_issues` |
| `403 Forbidden` | Missing `repo` scope | Re-auth: `gh auth login --scopes repo` |
