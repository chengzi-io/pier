# ADRS.md — Architecture Decision Record Specification

> Universal specification. Copy into any project. Project-specific trigger examples should be added to each project's own AGENT specification file.
> Companion locations: `docs/decisions/` (archive), `docs/decisions/README.md` (index).

## Why ADRs

ADR captures the **decision itself** (not the implementation) as text, enabling owners, new contributors, and future AI agents to quickly understand the project's evolution and hard constraints.

A well-written ADR answers three questions:

- What problem or constraint was faced? (`Context`)
- What did we choose? (`Decision`)
- What are the consequences of that choice? (`Consequences`, including negatives)

**Do not write implementation details, code snippets, or API field definitions.** Those belong in specs / API docs.

## File Location and Naming

- Specification: this file (root `ADRS.md`)
- Archive: `docs/decisions/`
- Index: `docs/decisions/README.md`
- Filename: `NNNN-kebab-case-title.md`, where NNNN is a four-digit sequential number (0001, 0002...)
  - Example: `0017-cache-invalidation-strategy.md`
  - Once published, numbers must never be reused, even if the ADR is superseded

## When an ADR Is Required (Trigger Checklist)

If any of the following conditions are met, the AGENT **must** create or update an ADR before modifying code:

| Trigger | Generic Example |
|---------|-----------------|
| Modifying an external API or data schema (adding/removing fields, changing semantics) | REST/gRPC fields, config file schema, protobuf definitions, SQL table structure |
| Modifying error code or error type contracts | Adding error codes, changing error-to-HTTP-status mapping, changing error response structure |
| Modifying behavioral semantics of executable contracts | BDD scenarios, integration test assertions, contract tests, OpenAPI specs |
| Modifying a state machine | Task/connection/cache/session state machines, lifecycle events |
| Modifying a core algorithm or strategy choice | Scheduling, load balancing, rate limiting, cache eviction, retry, backoff strategy |
| Modifying the security model | Authentication, authorization, scopes, key management, CORS, CSRF |
| Introducing or removing an external dependency | Third-party libraries, databases, message queues, SaaS, runtimes |
| Cross-module architectural changes | Changing module boundaries, inter-process communication, deployment topology, data flow direction |
| Modifying an external protocol contract | Protocol headers, version negotiation, serialization format, character encoding, compression |
| Performance or capacity baseline changes | Default timeouts, concurrency limits, batch sizes, cache capacity, rate limit thresholds |
| Modifying build, release, or deployment processes | CI/CD pipelines, image strategies, release cadence, blue-green/canary, rollback strategies |
| Modifying environment variables or startup parameters | Adding/removing/renaming env vars, CLI flag semantic changes |
| Modifying observability contracts | Log structure, metric label sets, trace fields, alerting rules |

**Does NOT trigger an ADR:**

- Bug fix (behavior restored to existing contract)
- Refactoring (external contract unchanged)
- Test additions
- Documentation wording adjustments
- Internal cross-reference updates

**When in doubt, write the ADR.** The cost of one record is far lower than one rollback.

> **Project-specific mapping**: Each project should map the abstract triggers above to concrete submodules and file paths in its own AGENT specification file (e.g., `AGENTS.md`), so the AGENT knows which areas of the project belong to which category.

## Template - Required ADR Fields

```markdown
# ADR-NNNN — Title

- **Date**: YYYY-MM-DD
- **Status**: proposed | accepted | deprecated | superseded by ADR-XXXX
- **Related**: spec file / feature file / upstream ADR (nullable)

## Context
Why is this decision needed now? Current constraints, pain points, external pressures.

## Decision
What we decided. One or two clear sentences, no code.

## Consequences
- **+** Positive consequences
- **-** Negative consequences / new constraints introduced / cost of reversal
- **neutral** Neutral observations

## Alternatives
Considered but not chosen options, each with a one-sentence rationale for rejection.

## Follow-ups
- Spec files that need updating
- BDD scenarios or contract tests that need adding/modifying
- Tests that need to be supplemented
```

## State Machine

```
proposed → accepted       (review passed, merged into main)
accepted → deprecated     (no longer recommended, but code retained)
accepted → superseded     (replaced by a new ADR; new ADR must reference old ADR number in Related)
```

- `proposed`: PR opened but not yet merged into main
- `accepted`: merged into main
- `rejected` status does not exist. Rejected alternatives should be preserved in the ADR's `Alternatives` section
- Supersede process: old ADR is never deleted or has its `Decision` modified; only its `Status` is changed to `superseded by ADR-XXXX`

## Writing Guidelines

- **Focus on the decision itself** — no code, no API details, no file paths. Code lives in source; API lives in specs
- **Declarative tone**: "We adopt X", not "X might be a better choice"
- **Avoid abstract slogans**: "for scalability" is not a reason; "because strategy X does not satisfy Z semantics in scenario Y, requiring an alternative" is
- **One ADR per decision**. Multiple related decisions get separate ADRs, cross-referenced in `Related`
- **Negative consequences must be honest**. Hidden costs become surprise items for owners six months later
- **Language**: consistent with the project's AGENT specification file (Simplified Chinese / English, etc.)

## Agent Maintenance Responsibilities

| Role | ADR Responsibility |
|------|-------------------|
| Lead Agent | Decision authority. When a trigger scenario occurs, **must** create or update the ADR first, then allow subagents to modify code. Implementation PRs without an ADR should not be merged. |
| Subagent | **Do not create or modify ADRs**. When discovering a task triggers an ADR scenario, stop immediately and report to the lead rather than deciding independently. |
| All Agents | Before writing code, read the `docs/decisions/README.md` index to confirm the relevant ADR is still `accepted`. `deprecated` or `superseded` ADRs **cannot** serve as decision basis. |

> "Lead / Subagent" roles are defined in the project's AGENT specification file. For projects without subagent collaboration, all ADR responsibilities fall on the contributor.

## Numbering and Index Maintenance

1. New ADR: scan `docs/decisions/NNNN-*.md`, take the max number + 1.
2. After writing: update the `docs/decisions/README.md` index. Only merge into main after `Status=accepted`.
3. Commit message linking (optional but recommended): `feat(scope): summary [ADR-0017]`.
4. Supersede process:
   - Create new ADR-X with `Status=proposed`, referencing old ADR-Y in `Related`
   - After new ADR is merged, change old ADR's `Status` to `superseded by ADR-X`
   - Gray out old ADR in `docs/decisions/README.md` index

## Relationship to Other Documents

```
spec / API doc        ← system WHAT and HOW
executable contract   ← executable form of WHAT (BDD / contract test / OpenAPI)
roadmap / plans       ← WHEN and WHY now
docs/decisions/       ← WHY this, not that  ← governed by this specification
ADRS.md               ← decision specification (rules)
```

ADR does not replace spec, and spec does not replace ADR.

- Spec describes **what** the system is
- ADR describes **why** it is this way

When an ADR's `Decision` changes, the spec must be updated accordingly (and vice versa, but spec changes should have an ADR first).
Executable contracts mirror the spec; semantic changes should be listed in the ADR's `Follow-ups`.

## Validation and Gates (Recommended Future Enforcement)

Currently does not block commits. Recommended: add a pre-push check in the project's git hook (`lefthook.yml` / `pre-commit-config.yaml` / husky, etc.):

- If the current diff modifies "external schema files" or "executable contract files" but `docs/decisions/` has no new/modified ADR, emit a warning (non-blocking).
- If `docs/decisions/README.md` index references a non-existent ADR file, raise an error.

> Each project should specify the concrete paths of "external schema files" and "executable contract files" in its AGENT specification file.
