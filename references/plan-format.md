# Plan Format Reference

The plan-to-project skill expects a markdown file structured with the KDTIX 5-level hierarchy.

## Hierarchy Levels

| Level | Marker Pattern | Example |
|-------|---------------|---------|
| Scope | `# Project Scope:` or `# PS-` | `# Project Scope: PS-001 My Project` |
| Initiative | `## Initiative:` or `## INIT-` | `## Initiative: INIT-001 My Initiative` |
| Epic | `### Epic:` or `### EP-` | `### Epic: EP-001 My Epic` |
| Story | `### Story:` or `### User Story:` | `### Story: Author the widget` |
| Task | `#### Task:` | `#### Task: Implement the parser` |

## Required Frontmatter Per Item

Each item should include the following attributes (as bold key-value pairs or blockquotes):

```
Priority: P0 | P1 | P2
Size: XS | S | M | L | XL
Blocking: #N, #M   (comma-separated issue references, optional)
```

## Minimal Example

```markdown
# Project Scope: PS-001 Build Widget Platform

## Initiative: INIT-001 Widget Core

### Epic: EP-001 Widget Engine
Priority: P0
Size: M

#### Story: Build parser
Priority: P0
Size: S

##### Task: Implement tokenizer
Priority: P0
Size: XS
```

## Parser Behavior

- Headers are matched case-insensitively
- Items without an explicit Priority default to `P1`
- Items without an explicit Size default to `M`
- Blocking references are extracted from `Blocking:` lines or `Blocks: #N` patterns
- The parser returns a dict:
  ```json
  {
    "scope": { "title": "...", "description": "...", "priority": "P0", "size": "M", "blocking": [] },
    "initiative": { ... },
    "epics": [ { ... } ],
    "stories": [ { "parent_ref": "EP-001", ... } ],
    "tasks": [ { "parent_ref": "Story title", ... } ]
  }
  ```
