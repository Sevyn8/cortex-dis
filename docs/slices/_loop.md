# DIS slice loop: operating guide

How a slice moves between the three roles, what each artifact must and must not
contain, and an example prompt for each. Operating manual for the slice build.
`build-guide.md` carries which slice is next and the order.

Roles:
- YOU: Sanjeev, operator. Decides; approves docs, plans, diffs, commits; pushes.
- CHAT: Claude AI. Drafts the slice doc and CC-facing prompts; reviews plans and CC's reports (diffs only as an escape hatch). Never writes repo code.
- CC: Claude Code. Plans and implements against the live repo. Can push back.

A note on sources. There is no single "source of truth." The live `ithina_dis_db`
schema, the code already written, and the docs are three sources, each can be stale
or wrong. The live DB is usually the most current for schema, but that is a reason
to reconcile across all three and surface mismatches, never a licence to stop
checking. Most of this slice's saves came from treating nothing as settled.

Who reads what. CC reads the live repo, the actual files, the introspected DB. CHAT
does not; CHAT reads only what is in this project's knowledge or attached to the
chat, which are snapshots that lag the repo. So CHAT uses project-knowledge docs
for process and orientation, and routes every live fact (schema, code, decision
state) through CC to verify against the repo. A project-knowledge doc is never
treated as current repo state.

Keep current in project knowledge: `_loop.md` (re-upload only when the loop
changes), and refreshed each slice boundary, `build-guide.md`, `decisions.md`,
`pilot-observations.md`, and the latest handoff.

---

## The loop (ASCII)

```
+===============================================================================+
|                  DIS SLICE LOOP: operator / chat / Claude Code               |
+===============================================================================+

  [0] PRE-SLICE  ............................................................ YOU
      - build-guide.md: which slice is next + order
      - register clean? (no stray OPEN item due this slice)
      - gather carry-ins from prior handoff / pilot-observations
                                   |
                                   v
  [1] DRAFT SLICE DOC  ......................................... CHAT  <-> YOU
      - first read (as snapshots, not facts): build-guide, decisions, prior
        handoff + pilot-observations, repo-structure/eng-ref, architecture,
        the code this slice builds on
      - goal-level: Depends-on, Goal, Task, Acceptance, Scope, Constraints, Open Qs
      - no schema specifics from snapshots; "derive from live DB" is a plan-mode task
      - fold carry-ins; mark deferred + trigger slice
      ---- gate: YOU approve ----
                                   |
                                   v
  [2] SAVE + COMMIT DOC  ............................. YOU (or CC)
      - docs/slices/slice-NN-<name>.md
      - commit with the implementation, or after it (not required before plan mode)
                                   |
                                   v
  [3] KICKOFF -> PLAN MODE  ............... CHAT writes -> YOU paste -> CC
      - files to read; reconcile DB/code/docs; plan contract; hard limits
      - CC: Shift+Tab x2 (plan mode = research only, no writes)
                                   |
                                   v
  [4] CC RETURNS PLAN  ............................................... CC
      order: (1) target safety (2) approach (3) risks (4) open Qs w/ evidence
             (5) impl steps (6) test plan: each criterion -> a check
                                   |
                                   v
  [5] REVIEW PLAN  ............................................ CHAT <-> YOU
      - CHAT feedback = arguments w/ load-bearing reasons, not directives
      - CC may push back / re-verify against repo
      ---- gate: YOU decide ----
         |                              |
   revise|                              |approve
         v                              v
   [5a] RE-PLAN (CC) --> back to [4]   [6] EXECUTE
                                   |
                                   v
  [6] EXECUTE  ...................... CHAT green-light -> CC (autonomous)
      - CC implements under auto edit approval; executable gates are the stop
      - no git until asked; load-bearing proofs ERROR (never skip) on absent dep
                                   |
                                   v
  [7] COMPLETION REPORT  ............... CHAT writes prompt -> CC reports
      - CC: full inventory account (files, deps, tests RUN + what each asserts,
        register touches). An account, not "all green". No git.
                                   |
                                   v
  [8] SELF-VALIDATE  ........... CHAT writes prompt -> CC (separate prompt)
      - CC re-derives correctness INDEPENDENT of the impl pass (re-introspect
        live DB, re-run gates), adversarial hunt, fixes in place. Still no git.
                                   |
                                   v
  [9] CHAT ASSESSMENT  ............................. CHAT (+ YOU)
      - reads the report + the self-validation; completeness, not pass/fail;
        names what a passing test could still miss
      - diffs read ONLY where they leave a property uncovered or semantic
        (escape hatch, not the default)
                                   |
                                   v
 [10] PRE-COMMIT GATE  ....................................... YOU
      - criteria pass? make check no FAIL?
      - register gaps logged this pass (own D-numbers)?
      - commit split decided? staged == intended?
                                   |
                                   v
 [11] COMMIT + MARK DONE  ........................ CC (or YOU)
      - scoped commits; mark slice DONE in build-guide.md
                                   |
                                   v
 [12] PUSH  ................................................. YOU
      - two workstreams on main: git fetch origin && rebase, THEN push
                                   |
                                   v
 [13] POST-SLICE LOG  ............................... CHAT <-> YOU
      - pilot-observations.md: what worked / where review earned its keep
      - feed fixes back into doc + prompt templates; update handoff
                                   |
                                   v
                            (next slice -> [0])

+-------------------------------------------------------------------------------+
| INVARIANTS (every slice)                                                      |
|  - DB, code, docs are three sources; reconcile, flag mismatches, settle none  |
|  - Live ithina_dis_db is usually most current for schema; verify, don't trust |
|  - Load-bearing claims carry inline evidence (file+line or introspected row)  |
|  - CHAT feedback is input to CC, not a verdict; CC verifies against the repo  |
|  - Postgres-touching slice: target-safety pass (5433/dis, never 5432/CM)      |
|  - Load-bearing proofs ERROR, never skip, when their dependency is absent      |
|  - New scope is a new slice, not silent expansion                             |
|  - Decisions get a register entry, not a workaround                           |
|  - Plan mode before code; tests in the same commit as code                    |
+-------------------------------------------------------------------------------+
```

---

## Artifacts, guidelines, example prompts

Each: who owns it, what it should and should not contain, an example to copy.

### 1. Slice doc (CHAT drafts, YOU approve)

Goal-level contract for one vertical slice. Says what to build and what is out of
bounds, never how. Saved to `docs/slices/slice-NN-<name>.md`.

Should have:
- Goal and task at intent level, not implementation.
- Acceptance criteria, scope boundary (in and out), constraints, open questions.
- A depends-on line; carry-ins folded in; deferred items with their trigger slice.
- "Derive from the live DB" written as an explicit plan-mode task.

Should not have:
- Schema specifics, column names, or file names asserted from snapshots.
- HTTP shapes, library versions, or how-to detail.
- Single-item bullet lists; em-dash.

Example (an open question that defers correctly):

```
4. dis-canonical coverage. Introspect the live canonical schema in ithina_dis_db
   (5433, not CM on 5432); the schemas/postgres/ DDL is informational and may
   differ. Enumerate which tables get models; derive each field (type,
   nullability, FK, enum) with introspected evidence. Assert no column from
   memory, DDL, or any snapshot.
```

### 2. Kickoff prompt (CHAT writes, YOU paste to CC)

Puts CC into plan mode. Self-contained: a fresh CC session needs nothing else.

Should have:
- Files to read in full.
- "Reconcile live DB, code, and docs; flag mismatches; cite path and line for code facts."
- Plan-mode only, no writes; the plan-output contract in order.
- A target-safety item if the slice touches Postgres.
- Hard limits; permission to re-submit a revised plan and iterate in plan mode.

Should not have:
- Implementation steps or design CC should derive.
- Repo facts asserted as settled.
- Scope beyond the slice.

Example:

```
You are working Slice N. Read in full: docs/slices/slice-NN-<name>.md, CLAUDE.md,
docs/{architecture,decisions,build-guide,repo-structure,engineering-reference}.md,
and <the code dirs/files this slice builds on>.

Reconcile three sources, none authoritative: the live ithina_dis_db schema
(5433), the code already written, and the docs. Any can be stale; cite path and
line for code facts, the introspected row for schema facts, and surface any
mismatch rather than silently picking one.

Plan mode only: research, no writes. Return a plan in order: (1) target safety
(read-only or where it writes; 5433 not 5432) (2) approach (3) risks surfaced
(4) open questions resolved with evidence inline (5) implementation steps
(6) test plan, each acceptance criterion -> one verification.

Hard limits: no DDL edits; no architecture changes; surface gaps, do not fix
them; stay inside the scope boundary. Re-submit a revised plan in plan mode if
feedback follows.
```

### 3. Plan-review feedback (CHAT writes, YOU relay to CC)

CHAT's read of the plan, phrased so CC can engage and disagree.

Should have:
- A framing line: arguments to evaluate, push back if wrong.
- Each item with its load-bearing reason.
- Repo-state claims routed back to CC to verify, not asserted.
- Approved-in-principle separated from change-requests.

Should not have:
- A directive or compliance tone.
- Repo facts stated as settled from CHAT's snapshot.
- Manufactured issues, or low-value nits bundled with the few that matter.

Example:

```
Reviewed your plan. These are arguments to evaluate, not directives; if the live
code or schema shows something I'm missing, push back.

1. I think the identifier aliases belong in dis-core, not dis-canonical.
   Reason: Slices 4-6 need them and none can depend on dis-canonical, so homing
   them there forces redefinition later. If 4-6 won't use them, say so.

Everything else approved in principle. Re-submit a revised plan (plan mode) that
takes these or argues against them.
```

### 4. Execute green-light (CHAT writes, YOU relay to CC)

Authorizes autonomous execution and names the gate, not a per-property review bar.

Should have:
- "Execute under auto edit approval; the executable gates are the stop."
- The load-bearing gate = what "done" means, stated as the checks that must be
  green (the invariant tests and lint), not a file-by-file review bar.
- Any single addition to fold in, with its reason.
- "Run no git until asked." A request that CC acknowledge before starting.

Should not have:
- A file-by-file checklist of everything.
- Re-litigation of settled plan items.
- Manual per-edit approval, now that gates carry the load.

Example:

```
Approved. Execute under auto edit approval; the gates are the stop. One addition:
<the load-bearing thing>, because <reason>. Run no git until I ask. Reply with a
brief ack before you start.
```

### 5. Diff watch-list (escape hatch only)

Not a default step. Reached only when the self-validation report (artifact 6)
leaves a property uncovered or semantic, where a diff read is the one way to
settle it. Most slices do not reach for this.

Should have:
- Load-bearing properties only, one line each (leaf-level errors, no duplication, re-export path held, model vs live columns, scope not leaked, no DDL edits).

Should not have:
- "Review every file."
- Cosmetic or formatting nits.
- Properties a test or gate already guarantees independently.

Example:

```
The report leaves these uncovered; read only them in the diff:
- the gate's condition means "any PII" not a narrowed form (semantic, no gate)
- no module-global engine introduced (review-only property, no test asserts it)
```

### 6. Completion-report prompt (CHAT writes, YOU relay to CC)

Forces an account of the work, not an outcome summary. A separate prompt from the
self-validation (artifact 7); this one comes first.

Should have:
- A demand for the full inventory: files created/edited/deleted, deps, tests run and what each asserts mapped to its criterion, register touches (D-number, OPEN/settled, cross-refs), anything beyond the approved plan.
- "Run no git until asked."
- Grouped by lib plus a docs section.

Should not have:
- Acceptance of "all green" as the answer.
- Outcomes standing in for an account.

Example:

```
Before any commit, and run NO git, give me a full inventory, not outcomes:
- Files created: path + one line each.
- Files edited: path + what changed and why.
- Files deleted/moved.
- Deps added: lib, package, version.
- Tests added: path + what each asserts -> criterion, and what the expected value
  is read FROM (independent of the code, or the same pass).
- Register: what you touched; per entry D-number, OPEN/settled, one-line text,
  cross-refs. If touched before commit stage, say so.
- Anything beyond the approved plan, called out.
Group by lib plus a docs section.
```

### 7. Adversarial self-validation prompt (CHAT writes, YOU relay to CC)

A separate prompt, relayed after the completion report and before any commit.
Makes CC check its own work from a vantage independent of the implementation pass,
and fix what it finds. This is the per-slice instrument that replaced diff-watching.

Should have:
- "Run no git until asked."
- "Re-derive, do not recall": re-introspect the live DB for every schema fact the
  code depends on; re-run the gates fresh.
- A named hunt list of the silent-failure properties (the ones a green suite can
  hide), each demanding evidence-it-holds-this-pass or a flag-and-fix.
- "What could a passing test STILL miss": force CC to name any test+code from one
  pass (shared blind spot) and how it ruled the omission out, or that it could not.
- "Do not manufacture issues; if a property holds, show evidence and move on."

Should not have:
- A request to recap, or to re-read its own diff and agree with it.
- Acceptance of green as proof.

Example:

```
Validate your own work adversarially. No git. Do not recap or re-read your diff
and agree with it. Re-derive correctness from sources INDEPENDENT of the impl
pass: re-introspect the live DB for every schema fact the code depends on; re-run
the gates fresh. Hunt by name (evidence it holds this pass, or flag + fix): <the
silent-failure properties for this slice>. Then: what could a passing test STILL
miss? Name any test+code from one pass and how you ruled the omission out, or that
you could not. Do not manufacture issues. Report issues found+fixed, found+left
with reason, properties confirmed clean with evidence, re-run gate numbers.
```

### 8. CC's report + self-validation, and CHAT's assessment

CC reports what landed (artifact 6) and self-validates (artifact 7). CHAT judges
completeness across both, not just pass/fail.

CHAT should:
- Ask what a passing test could still miss (independent pull vs shared blind spot).
- Flag register gaps before commit.
- Confirm scope held.

CHAT should not:
- Treat green as complete.

Example:

```
"Criterion 6 passes" proves the model agrees with its own test, not that it is
column-complete. If both came from one introspection pass, a missing column is
absent from both and passes. Re-pull information_schema independently and assert
exact set equality both directions, per table.
```

### 9. Commit-gate prompt (CHAT writes, YOU relay to CC)

Relayed only after the pre-commit gate (step 10) passes. Authorizes git, demands a
scoped split, and withholds the push.

Should have:
- "Now you may run git", only after the gate is clean.
- A scoped-increment split (one concern per commit), each buildable on its own.
- "Show git status --short + the intended split before staging, so I can confirm
  staged == intended."
- Where the slice is marked DONE (build-guide, status word only).
- "Do NOT push" (push stays with the operator). Report hashes after.

Should not have:
- A one-blob commit.
- A push.
- Staging before the split is shown and confirmed.

Example:

```
Slice 4 approved for commit. Now you may run git. Commit in scoped increments,
not one blob:
1. dis-core errors (RlsContextError, PiiBackendNotConfiguredError, StorageError).
2. dis-rls (lib + tests).
3. dis-pii (lib + tests).
4. dis-storage (lib + tests).
5. Workspace wiring (root pyproject.toml, uv.lock): group with whichever commit
   makes the tree buildable at each step; state how you ordered it.
6. docs: D40, D41 in decisions.md; the three per-lib CLAUDE.md files.
Then mark Slice 4 DONE in build-guide.md (status word only, in place).
Before staging: show me `git status --short` and your intended commit split (which
files in which commit) so I can confirm staged == intended. Do NOT push; push
stays with me. After commits, give me the commit hashes and confirm nothing
unintended is staged or committed.
```

### 10. Commit-approval prompt (CHAT writes, YOU relay to CC)

CHAT's read of CC's proposed split, before CC stages. Approves the buildable split,
names adjustments with reasons, and holds the push.

Should have:
- Explicit approval of the split (and the buildable-at-each-commit reasoning, if CC
  defended it).
- Numbered adjustments, each with its reason (what to add, what to keep out).
- "Stage one commit at a time; show git status --short before each."
- "Still no push; report hashes."

Should not have:
- Silent acceptance of an unscoped or unbuildable split.
- Approval of a push.

Example:

```
Split approved, commits 1-5 as proposed; the distributed workspace wiring is
correct (buildable at each commit). Three adjustments before you stage:
1. Include the slice doc. docs/slices/slice-04-data-plane-safety.md goes in, to
   match Slices 1-3. Put it as its own commit BEFORE commit 1
   (docs(slice-4): slice contract), since the doc predates the code and loop step
   [2] is save+commit doc. Renumber the rest after it.
2. Keep pilot-observations.md and _loop.md OUT (committed separately later);
   confirm neither is staged in any commit.
3. Commit 5: confirm in the staged diff that docs/build-guide.md changed ONLY the
   Slice 4 status word (TODO -> DONE), and that docs/decisions.md D40 reads as
   gate-in-force AND posture-OPEN and D41 as OPEN (Slice 7 deadline).
Stage one commit at a time; before each, show me git status --short so I see
staged == intended. Still no push. Report hashes as you go.
```

### 11. New-slice kickoff for CHAT (YOU paste into a fresh CHAT)

Starts a slice in a fresh Claude AI session. Grounds CHAT in docs, the live DB,
and the code already written, not the docs alone.

Should have:
- Pointer to `_loop.md` and `build-guide.md`.
- "Read everything in project knowledge" (so nothing like CLAUDE.md is skipped), all as lagging snapshots.
- The named draft-time read-list (below) as the priority set within that.
- Pull carry-ins and due OPEN register items.
- Reconcile-DB-code-docs and brainstorm-shape rules.
- "Don't draft until I say go."

Should not have:
- A request to draft immediately.
- Any assumption the repo is greenfield.
- Schema or code specifics treated as known.

Draft-time read-list (what CHAT reads before drafting, all as snapshots to
orient, not facts to assert):
- `build-guide.md`: which slice is next, its stated scope, the order.
- `decisions.md`: OPEN items due this slice, decisions the slice must honor, gaps to register.
- prior handoff + `pilot-observations.md`: carry-ins and lessons (these live here, not in build-guide).
- `repo-structure.md` / `engineering-reference.md`: the dirs the slice lands in and what it depends on.
- `architecture.md`: where the slice sits and what consumes it (sizes the scope boundary).
- the already-written code this slice builds on or extends: interfaces, conventions, surface not to disturb.

Note: CHAT reads only project knowledge and what you attach, never the repo. Keep
`_loop.md`, `build-guide.md`, the latest handoff, and `pilot-observations.md` in
project knowledge, or CHAT reasons from stale or absent snapshots. Live facts go
through CC.

Example:

```
Starting the next DIS slice. You are CHAT in the slice loop: you draft the slice
doc and the CC-facing prompts, and review plans and diffs; you never write repo
code. Read _loop.md (in project knowledge) first; build-guide.md carries which
slice is next.

Mode: brainstorm. Before drafting:
1. Read everything in project knowledge (don't skip any, including CLAUDE.md),
   all as lagging snapshots to orient, not facts to assert. Priority set:
   build-guide (next slice + scope), decisions.md (OPEN items due this slice,
   decisions to honor, gaps to register), the prior handoff + pilot-observations
   (carry-ins), repo-structure / engineering-reference (dirs + dependencies),
   architecture (where the slice sits, what consumes it), and the already-written
   code this slice builds on. Confirm which slice is next and its scope.
2. Pull carry-ins from the prior handoff / pilot-observations, plus any OPEN
   register item due this slice.
3. Tell me: what you'd pin vs leave open; anything ambiguous or risky in scope;
   any register gap this slice should resolve.

Hold these rules:
- DB, code, docs are three sources, none authoritative; reconcile, flag
  mismatches, settle none. Verify schema against the live DB; don't assert it
  from snapshots. "Derive from live DB" is a plan-mode task, not yours to resolve.
- Read the code prior slices built (modules, interfaces, conventions, the surface
  this slice depends on); don't draft as if the repo were greenfield; confirm code
  specifics against the files, not memory.
- Slice doc stays goal-level; no how, no column/file names as fact, no versions
  or HTTP shapes.
- New scope is a new slice; mark deferred items with their trigger.
- When unsure, say "I'm not sure" first; route repo facts to be verified.

Formatting: no em-dash; questions one at a time, numbered Q1/X; brainstorm shape.

Don't draft the slice doc until I say go. Start with step 1: which slice is next,
and what you'd pin vs leave open.
```

---

## Three failure points this loop gates

All bit a real slice; all are now explicit nodes:

- Step 7: force the inventory. "All green" is an outcome, not an account. CHAT assesses completeness at step 9.
- Step 8: a load-bearing proof that SKIPS on an absent dependency reports green without having run. Make such proofs ERROR at setup, never skip, and never fall back to a guessed default. Surfaced in Slice 4 (the RLS isolation test), caught by self-validation.
- Step 10: log register gaps with their own D-numbers before the commit, not after. A decision in force with no entry is a loose end the next slice inherits.
