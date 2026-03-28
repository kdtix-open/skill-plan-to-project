# GitHub GraphQL Reference

GraphQL queries and mutations used by the plan-to-project scripts.

## Get Issue Type IDs for an Org

```graphql
query GetIssueTypes($org: String!) {
  organization(login: $org) {
    issueTypes(first: 20) {
      nodes {
        id
        name
      }
    }
  }
}
```

**gh CLI invocation:**
```bash
gh api graphql -f query='
query($org: String!) {
  organization(login: $org) {
    issueTypes(first: 20) {
      nodes { id name }
    }
  }
}' -f org="kdtix-open"
```

## Get Project V2 Field IDs

```graphql
query GetProjectFields($org: String!, $number: Int!) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      fields(first: 30) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
        }
      }
    }
  }
}
```

## Add Item to Project V2

```graphql
mutation AddItem($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: { projectId: $projectId, contentId: $contentId }) {
    item { id }
  }
}
```

## Set Single-Select Field (Priority / Size / Status)

```graphql
mutation SetField($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { singleSelectOptionId: $optionId }
  }) {
    projectV2Item { id }
  }
}
```

## Assign Issue Type

```graphql
mutation SetIssueType($issueId: ID!, $issueTypeId: ID!) {
  updateIssue(input: { id: $issueId, issueTypeId: $issueTypeId }) {
    issue { id issueType { name } }
  }
}
```

## Get Issue nodeId and databaseId by Number

```bash
gh api repos/{owner}/{repo}/issues/{number} \
  --jq '{nodeId: .node_id, databaseId: .id, number: .number}'
```

## Notes

- All GraphQL calls use `text=True, encoding='utf-8'` in subprocess to avoid encoding issues.
- Prefer `gh api graphql` over direct `curl` — handles auth token automatically.
- `issueTypes` query requires org-level admin access or `read:org` scope.
