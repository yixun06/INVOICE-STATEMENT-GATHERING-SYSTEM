# InvoiceGather Multi-Agent Development Workflow

This is the binding development workflow for InvoiceGather work performed by
Codex and Antigravity. It protects source truth, business contracts, approved
UX, Git history, and each agent's work.

## 1. Team model and core principle

| Role | Authority and responsibility |
| --- | --- |
| User | Product Owner, business authority, and final acceptance authority. Decides business rules, financial semantics, source interpretation, schema, product scope, and disputed cross-agent decisions. |
| Codex | Primary Development Agent: Senior Software Engineer and Technical Architect. Approximate long-term ownership: 70%. |
| Antigravity | Secondary Experience Agent: UI Engineer and UX Reviewer. Approximate long-term ownership: 30%. |

The percentages are guidance, not quotas.

> **CODEX BUILDS PRODUCT CAPABILITY.**
>
> **ANTIGRAVITY REFINES USER EXPERIENCE.**
>
> **SAME SCOPE, SAME TIME = ONE IMPLEMENTATION OWNER.**
>
> **ANTIGRAVITY MAY SIMPLIFY PRESENTATION, BUT MAY NOT SIMPLIFY TRUTH.**

The boundary is correctness and business contract versus user experience and
presentation. Codex is not merely a backend agent: it owns frontend state and
business wiring where needed. Antigravity may implement frontend presentation,
but it does not own correctness semantics by default.

## 2. Ownership matrix

| Area | Default ownership | Decision/review rule |
| --- | --- | --- |
| Business rules; source interpretation; user acceptance | User approval | Neither agent changes these autonomously. |
| Financial semantics; reconciliation; source authority | Codex primary | User approval for a changed or unclear rule. |
| Architecture; database; schema; persistence; security; performance | Codex primary | Schema and consequential architecture changes require user approval. |
| Parsers; validation; service/domain contracts; tests; integration safety | Codex primary | Antigravity reviews presentation effects only. |
| Frontend business/state logic | Codex primary | Antigravity must not duplicate or bypass it. |
| Information architecture; navigation; UX flow; visual hierarchy | Antigravity primary | Codex reviews technical impact without redesigning approved UX. |
| CSS/presentation; accessibility; responsive behavior; animation; design system | Antigravity primary | Must preserve business/state contracts. |
| Material cross-boundary change | Shared review required | Stop and obtain the required engineering/business decision before implementation. |

Codex is the default implementation owner for new product capability. Antigravity
is not the default owner of a new business capability.

## 3. Worktrees and branches

Repository: `D:/OneDrive/UMPSA DOC/INTERN/zenxin/InvoiceGather`

Remote: `https://github.com/yixun06/INVOICE-STATEMENT-GATHERING-SYSTEM.git`

Current integration/development branch: `feature/uat2-invoice-persistence-integration`

Protected historical branch: `release/uat-v1` — **never modify it**.

Prefer separate worktrees for concurrent work:

```text
InvoiceGather/       integration / Codex worktree
InvoiceGather-UX/    Antigravity UX worktree
```

They are worktrees of the same Git repository, but each uses its own branch.
No agent may switch the other worktree's branch or touch its uncommitted files.
Worktrees prevent filesystem collisions; they do not replace the ownership and
semantic rules in this document.

Use `codex/<scope>` for new engineering branches, unless an existing feature
branch already owns the work. Use `ux/<scope>` for UX branches, for example
`ux/weekly-billing-v1`, `ux/invoice-review-v1`, or `ux/navigation-v1`.

When a separate worktree is unavailable, treat the shared worktree as if it
may contain another agent's work. Inspect status first, preserve every
unrelated change, and make only the agreed scoped edit.

## 4. Scope boundary and autonomy

Antigravity must not autonomously expand a UX task into a backend redesign,
database/schema change, service architecture change, parser modification,
persistence behavior, reconciliation rule, financial calculation, Product
Master authority change, new business workflow, or unrequested product
feature.

If a UX goal requires one of those changes, stop and report:

```text
ENGINEERING / BUSINESS DISCUSSION REQUIRED

Desired UX behavior:
Why the current contract prevents it:
Exact contract affected:
Minimum engineering decision needed:
```

Codex must also respect approved UX ownership. Technical review may require a
change only when UX work changes business behavior, duplicates business logic,
bypasses validation, violates a service contract, causes a security/integrity
issue, breaks tests, or changes persistence semantics. It is not permission to
replace approved presentation with a simpler engineering design.

Project-wide autonomy policy:

| Action | Autonomous? |
| --- | --- |
| Testing and diagnosis | Yes |
| Technical bug fix with an already-locked result | Yes |
| Business-rule change | No |
| Schema change | No |
| Source interpretation change | No |
| Make-it-green workaround | No |

Use `SCHEMA DECISION REQUIRED` when a schema decision is genuinely needed and
`BUSINESS DECISION REQUIRED` when business semantics are ambiguous. Never
widen tolerance to pass, turn missing into zero, add fuzzy matching without
approval, change source authority, guess financial meaning, or bypass atomicity
for convenience.

+### UX scope control and user confirmation

Antigravity is not authorized to optimize InvoiceGather toward its own idea of
"perfect UX." Its goal is to make the approved workflow clearer, faster,
safer, and easier to use while preserving real staff habits, approved scope,
engineering contracts, and implementation proportionality.

Apply the proportional UX rule: make the smallest change that solves the
highest-impact user problem. Do not redesign usable areas for aesthetic
consistency, introduce a new interaction paradigm, or expand scope because a
theoretical UX may be better. InvoiceGather is an internal productivity tool:
success means fewer mistakes, faster completion, clearer system state, lower
cognitive load, easier exception recovery, stronger confidence, and preserved
auditability—not visual novelty or an Apple-like, Linear-like, modern-SaaS, or
maximally animated aesthetic.

Before each UX implementation, Antigravity must state:

```text
PRIMARY UX PROBLEM:
APPROVED SCOPE:
FILES EXPECTED TO CHANGE:
USER-VISIBLE BEHAVIOR EXPECTED TO CHANGE:
USER-VISIBLE BEHAVIOR THAT MUST NOT CHANGE:
OUT-OF-SCOPE AREAS:
```

Classify proposed work as `REQUIRED` (directly solves the approved usability
problem), `OPTIONAL` (low-risk polish clearly within scope), or `OUT OF SCOPE`.
Implement REQUIRED work; implement OPTIONAL work only under those conditions;
do not implement OUT OF SCOPE work. Record it as `FUTURE UX OPPORTUNITIES` for
later review.

The following require user UX approval before implementation: major page or
navigation restructuring; moving workflows between pages; combining or
splitting screens; replacing tabs; new wizards; hiding information or removing
controls; materially changing action priority; ambiguous business terminology;
persistent drawers/side panels; major table interactions; new filters/search
models; significant animation; global style changes; unrequested dashboards or
charts; large mobile redesigns; new user journeys; or new UX concepts.

For one of those decisions, stop and provide:

```text
UX DECISION REQUIRED

1. Current behavior
2. Observed problem
3. Option A
4. Option B
5. Optional recommended option
6. Trade-offs
7. Engineering impact
8. What will remain unchanged
```

Wait for user approval. Do not redesign first and ask afterward. Likewise, if
multiple reasonable choices depend on staff, Finance, Operations, information
visibility, terminology, or other user preference, report `UX DECISION
REQUIRED`; do not choose the more fashionable or technically elegant option.

Antigravity may decide obvious, low-risk refinements that remain within an
approved task: spacing/alignment/typography, table-width and wrapping fixes,
RM alignment, icon and contrast consistency, clearer loading or empty wording,
accessibility labels, minor button spacing, an already-approved technical
disclosure, or visual distinction between locked TOTAL/SUBTOTAL/DETAIL rows.
It is expected to ask user UX questions whenever this boundary is reached.
Completion means that the approved usability problem is solved, not that no
further polish can be imagined.

## 5. Contract priority and InvoiceGather example

Resolve conflicts in this order:

1. Explicit user-approved business decision
2. Authoritative source evidence
3. Locked business/data contract
4. Approved schema
5. Correctness/regression tests
6. Service/domain contract
7. Current implementation
8. Documentation
9. Agent recommendation

A cleaner UI does not override a business contract. An easier implementation
does not override an approved UX contract. If authoritative layers genuinely
conflict, stop and escalate to the user; do not silently choose one.

Example: if the backend provides `identity_scope = GROUP` and
`merchandise_reconciled = true`, Antigravity may change the label, icon, color,
location, explanation, or progressive disclosure. It may not change GROUP
eligibility or meaning, the reconciliation result, the backend enum, or the
matching calculation. If GROUP is unclear, ask; UX intuition is not source
authority.

## 6. Default feature flow

Use this flow for a material user-facing change. A trivial backend-only change
does not require a UX cycle.

```text
Codex builds feature
  -> correctness tests
  -> real functional smoke
  -> commit + push
  -> stable engineering baseline
  -> Antigravity starts from the exact approved commit
  -> UX branch/worktree
  -> UX implementation
  -> browser and visual QA
  -> commit + push UX branch
  -> Codex technical impact review
  -> user real-workflow acceptance
  -> merge
```

Automated tests never replace practical user acceptance for material flows such
as upload, review, commit, billing, and export.

## 7. Handoff contract

Every cross-agent implementation handoff must use this template:

```text
HANDOFF

BASE COMMIT:
FINAL COMMIT:
BRANCH:
WORKTREE:
PUSH RESULT:

CHANGED FILES:
WHAT CHANGED:
WHAT MUST NOT HAVE CHANGED:

LOCKED BUSINESS CONTRACTS:
LOCKED UX CONTRACTS:
SERVICE / DATA INTERFACES THE NEXT AGENT MAY RELY ON:

TESTS RUN:
REAL SMOKE RESULT:
KNOWN LIMITATIONS:
UNRESOLVED QUESTIONS:

WORKTREE STATUS:
```

The receiving agent must verify the stated base/final commits and inspect the
scope before modifying files.

## 8. Stale-base protection

Before Antigravity begins UX work, it must verify that the exact approved base
commit exists, its branch is based on that commit, the active engineering work
is complete, and its UX worktree is clean.

If the integration branch advances after UX work begins, do not automatically
rebase. First determine whether the new commits affect the UX scope. If they
do, report `BASELINE UPDATE REVIEW REQUIRED` before bringing those changes
into the UX branch.

## 9. Review responsibilities

After an Antigravity UX implementation, Codex checks that business logic and
financial calculations are unchanged; persistence is unchanged; validation is
not bypassed; service contracts are respected; UI does not duplicate business
calculation; no hidden state mutation occurs; security/integrity is unaffected;
and regression tests pass. Classify each outcome as:

```text
BLOCKING TECHNICAL ISSUE
NON-BLOCKING TECHNICAL CONCERN
NO TECHNICAL ISSUE
```

Codex must not redesign presentation during this review.

Antigravity may review Codex-created user-facing work for workflow friction,
hierarchy, terminology, loading/error/empty/success states, accessibility,
information density, responsiveness, and presentation consistency. It must
classify findings as either `UX ISSUE` or `BUSINESS / ENGINEERING QUESTION`
and must not silently fix the latter.

Codex remains primary owner of correctness/regression testing. Antigravity is
responsible for browser smoke, screenshot/visual QA, interaction QA,
practical accessibility checks, and relevant existing tests after UX changes.
It must never weaken tests to make a UX change pass.

## 10. Git safety

Before implementation, run:

```text
git status --short
git branch --show-current
git log --oneline -10
git diff
git diff --cached
```

After implementation, run the applicable tests, `git diff --check`, inspect
the staged diff, create a focused commit, and push normally.

Unless the user explicitly approves, never run:

```text
git reset --hard
git clean
force push
destructive rebase
```

Do not stage unrelated or untracked files. Do not automatically stash another
agent's work. Preserve existing temporary, generated, test, and UX files unless
the user explicitly authorizes their removal.

## 11. Model and review efficiency

Use a high-reasoning Codex model for substantial engineering work. Use
Antigravity Flash Medium by default for UX audit, UI implementation, and visual
QA. Escalate Antigravity only when deeper information-architecture or product
reasoning is actually required.

Use milestone-based or targeted UX reviews. Do not mechanically perform a full
UX audit for every sprint or trivial engineering change.

## 12. First formal pilot

The first formal pilot after this document is committed is **Weekly Billing UX
refinement**.

Engineering behavior baseline: `efd9ae5` (`feat: add weekly billing financial
summary`). The future Antigravity task begins only after this document's commit
hash is provided. It must use an approved UX branch/worktree and follow the
stale-base protection above.

This task does not create that UX branch or worktree.
