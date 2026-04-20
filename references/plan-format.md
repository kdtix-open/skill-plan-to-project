# Plan Format Reference

The plan-to-project skill expects a markdown file structured with the KDTIX 5-level hierarchy.

## Hierarchy Levels

| Level | Marker Pattern | Example |
|-------|---------------|---------|
| Scope | `# Project Scope:` or `# PS-` | `# Project Scope: PS-001 My Project` |
| Initiative | `## Initiative:` or `## INIT-` | `## Initiative: INIT-001 My Initiative` |
| Epic | `### Epic:` or `### EP-` | `### Epic: EP-001 My Epic` |
| Story | `### Story:`, `### User Story:`, `#### Story:`, or `#### User Story:` | `### Story: Author the widget` |
| Task | `#### Task:` or `##### Task:` | `#### Task: Implement the parser` |

## Required Frontmatter Per Item

Each item should include the following attributes (as bold key-value pairs or blockquotes):

```
Priority: P0 | P1 | P2
Size: XS | S | M | L | XL
Blocks: #123, #160      (optional, comma-separated issue references)
Blocking: #123, #160    (optional alias of Blocks:, same semantics)
```

`Blocks:` and `Blocking:` are treated as aliases. In both cases, the current
item is the blocker, and the referenced issues are the issues it blocks.

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
- Story and task headers accept both the compact documented depth and the deeper nested depth used by older examples
- Items without an explicit Priority default to `P1`
- Items without an explicit Size default to `M`
- Blocking references are extracted from `Blocks:` and `Blocking:` lines
- `Blocks:` / `Blocking:` means the current item blocks the referenced issue(s)
- `#123` references are resolved against existing GitHub issue numbers in the
  target repository
- Text references are resolved against parsed issue titles in the current
  manifest
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
