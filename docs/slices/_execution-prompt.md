# Execution prompt (standing template for all slices)

This is the standing prompt for every slice plan-mode session. Author it once;
reuse it per slice by filling the two-line invocation at the bottom. It holds
the plan-output contract so the slice docs stay durable build contracts and
CLAUDE.md stays lean.

---

## Standing instructions

You are working slice `{NN}`. Before doing anything:

1. Read these files in full:
   - `docs/slices/slice-{NN}-{name}.md` (the contract for what to build)
   - `CLAUDE.md` (project-wide invariants)
   - `docs/architecture.md`, `docs/decisions.md`, `docs/build-guide.md`,
     `docs/repo-structure.md`, `docs/engineering-reference.md`
   - Any service or lib `README.md` and `CLAUDE.md` named in the slice
   - Any source files the slice points at (for Slice 1: `schemas/postgres/`)
2. Enter plan mode (Shift+Tab twice). Plan mode is research and analysis only.
   No file writes, no code, no edits.
3. Return a plan, not an implementation.

## Plan-output contract

The returned plan must contain, in this order:

1. **Approach.** How you will implement the slice, at the level of files
   touched and the mechanism for each. No code, no library versions unless the
   slice fixes them.
2. **Problems and risks surfaced.** Anything that could break the slice:
   missing or wrong files, contradictions between the slice and the repo,
   environment-portability risks, ordering or dependency hazards. Name them;
   do not silently work around them.
3. **Open questions resolved.** Answer every item in the slice's Open Questions
   section against the actual repo. For each: what you found, and what it means
   for the plan. If a question cannot be resolved from the repo, say so.
4. **Implementation steps.** Ordered steps you would execute on approval.
5. **Test plan.** Map each acceptance criterion in the slice to exactly how it
   will be verified (command, query, or check). Every criterion gets a line.
   If a criterion cannot be verified as written, flag it.
6. **Destructive-action and target safety.** Before any other plan content,
   state: (a) which database, host, and port every command in this slice
   touches, resolved from the actual env/config files, not assumed; (b) every
   destructive or irreversible action the slice performs (DROP, TRUNCATE,
   DELETE, CREATE/DROP DATABASE, file overwrite, data migration); and (c) for
   each, what prevents it from hitting the wrong target. If the slice touches
   Postgres, confirm it targets the DIS database/port and cannot reach Customer
   Master (5432/ithina_platform_db). If a guard is warranted, name it. If the
   slice is read-only with no destructive actions, say so explicitly.

## Hard limits

- Do not author new DDL, contracts, or schema. If a required file is missing or
  wrong, surface it in the plan; do not fix it in this slice.
- Do not propose architecture changes. If the slice forces one, stop and raise
  it; do not fold it into the plan.
- Do not exceed the slice's scope boundary. New scope is a new slice.
- Plan mode writes nothing. The plan is text returned for review.

---

## Per-slice invocation

Paste this after the standing instructions are in context, or just paste both:

```
Read docs/slices/slice-{NN}-{name}.md and the files named in the standing
instructions. Enter plan mode and plan slice {NN} per the plan-output contract.
```

For Slice 1:

```
Read docs/slices/slice-01-bootstrap-migration.md and the files named in the
standing instructions. Enter plan mode and plan slice 01 per the plan-output
contract.
```
