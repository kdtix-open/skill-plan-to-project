# gh CLI Patterns Reference

Reliable patterns for using `gh` CLI in Python scripts.

## Subprocess Best Practices

Always use `text=True, encoding="utf-8"` to avoid encoding issues on all platforms:

```python
import subprocess, sys

result = subprocess.run(
    ["gh", "issue", "create", "--repo", repo, "--title", title, "--body-file", body_file],
    text=True,
    encoding="utf-8",
    capture_output=True,
)
if result.returncode != 0:
    print(f"[ERROR] {result.stderr}", file=sys.stderr)
    sys.exit(result.returncode)
url = result.stdout.strip()
```

## Create an Issue (body from file)

```python
import tempfile, os

def create_issue(repo: str, title: str, body: str) -> str:
    """Returns the issue URL."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                     delete=False, encoding="utf-8") as f:
        f.write(body)
        tmp = f.name
    try:
        result = subprocess.run(
            ["gh", "issue", "create", "--repo", repo,
             "--title", title, "--body-file", tmp],
            text=True, encoding="utf-8", capture_output=True,
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(1)
        return result.stdout.strip()
    finally:
        os.unlink(tmp)
```

> Always use `--body-file` — never pass body as a shell argument. Multi-line markdown bodies
> break shell quoting and produce silent truncation or injection.

## Get Issue Metadata (nodeId + databaseId)

```python
import json

def get_issue_ids(repo: str, number: int) -> dict:
    result = subprocess.run(
        ["gh", "api", f"/repos/{repo}/issues/{number}",
         "--jq", "{nodeId: .node_id, databaseId: .id, number: .number}"],
        text=True, encoding="utf-8", capture_output=True,
    )
    return json.loads(result.stdout.strip())
```

## Add a Label to an Issue

```python
def add_label(repo: str, number: int, label: str) -> None:
    subprocess.run(
        ["gh", "issue", "edit", str(number), "--repo", repo, "--add-label", label],
        text=True, encoding="utf-8", check=True,
    )
```

## Update Issue Body

```python
def update_body(repo: str, number: int, body: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                     delete=False, encoding="utf-8") as f:
        f.write(body)
        tmp = f.name
    try:
        subprocess.run(
            ["gh", "issue", "edit", str(number), "--repo", repo, "--body-file", tmp],
            text=True, encoding="utf-8", check=True,
        )
    finally:
        os.unlink(tmp)
```

## Get Current Issue Body

```python
def get_body(repo: str, number: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", str(number), "--repo", repo, "--json", "body", "--jq", ".body"],
        text=True, encoding="utf-8", capture_output=True,
    )
    return result.stdout.strip()
```

## Rate Limiting

- Add `time.sleep(0.5)` between `gh issue create` calls to avoid hitting secondary rate limits.
- For bulk field mutations, add `time.sleep(0.1)` between GraphQL calls.

## Auth Check

```python
def check_auth() -> None:
    result = subprocess.run(["gh", "auth", "status"], text=True, encoding="utf-8",
                             capture_output=True)
    if result.returncode != 0:
        print("[ERROR] gh not authenticated. Run: gh auth login", file=sys.stderr)
        sys.exit(1)
```
