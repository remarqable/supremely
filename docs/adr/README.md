# Architecture Decision Records

Supremely uses Architecture Decision Records (ADRs) for decisions that materially affect the system.

ADRs capture **why** a decision was made, not merely what the current code does.

## When to write an ADR

Use an ADR when a decision:

- establishes or changes a major architectural boundary;
- introduces an important dependency or infrastructure choice;
- changes a security or data model;
- establishes a convention that future work must follow;
- reverses an earlier architectural decision.

Routine implementation choices do not need ADRs.

## Suggested format

```markdown
# ADR-NNN: Title

- Status: Proposed | Accepted | Superseded
- Date: YYYY-MM-DD

## Context

What problem or constraint requires a decision?

## Decision

What are we choosing?

## Consequences

What becomes easier, harder, possible, or constrained?

## Alternatives considered

What meaningful alternatives were rejected, and why?
```

Number ADRs sequentially: `0001-short-title.md`, `0002-short-title.md`, and so on.

Do not rewrite accepted ADRs to make history look cleaner. If the decision changes, supersede it with a new ADR.
