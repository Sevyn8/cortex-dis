# _loop_extn_01.md: overload-day overlay for _loop.md

An opt-in overlay to the build loop, for days when the Claude API is throttling or sessions wedge.
It is DORMANT by default. It changes how steps execute, never whether they are verified.

## How to invoke

Operator says: "follow _loop.md with _loop_extn_01.md". CHAT then runs the normal loop but applies
the overrides below wherever the API is degraded. CHAT may also propose switching to this overlay on
its own when it sees the trigger signs, but only the operator confirms the switch.

## When it applies (the trigger)

Turn it on when any of these appear, AND a real failure has first been ruled out (a wedged or
failing turn can be a genuine bug, e.g. a hanging test, not the API; confirm it is throttling, not
broken code, before attributing it here):
- 529 Overloaded errors, repeated, not a one-off.
- A CC turn wedges or spins on retries during a long generation (planning, multi-file builds);
  token counters climbing into the tens of thousands on a turn that should be quick.
- Auto mode reports it cannot run a bash command because the model/safety-classifier is unavailable
  ("cannot determine the safety of Bash right now"). File reads/searches still work; bash does not.

Turn it off (revert to plain _loop.md) once turns complete normally again.

Stop condition: if even minimal turns (a one-line request, a file list) fail, it is a full incident;
stop and wait, no workaround helps. Check status.claude.com.

## The one principle

Small turns get through; large turns wedge. The wedging clustered on turns past roughly 30k tokens
and on accumulated session context (treat ~30k as a rough ceiling, not a hard line). So: keep every
CC turn small, write large outputs to disk in pieces, and move anything that does not need the model
(running tests, gates, git) off CC entirely.

## The overrides

### 1. Large outputs (plans, inventories, long reports) → write to a scratch file, one section per turn
Do not ask CC for a big single-turn generation. Have it write to a scratch file
(e.g. docs/slices/<slice>-plan.md) one section at a time:
- First turn: "Create the file, write ONLY <section 1>, stop."
- Each next turn: "APPEND to <file>, <section N> under a new heading, stop."
- Use a unique heading per section so it stacks, not overwrites. The verb is "Append to", never
  "write the plan".
- Confirm stacking cheaply between turns: `grep '^## ' <file>` should gain one heading per turn.
- If a turn fails mid-append, re-run that one section (idempotent by unique heading) and verify with
  the grep before continuing.
- The scratch plan is a throwaway: gitignored or deleted after review, never a committed deliverable
  (the slice doc and decisions register hold the durable record).
Writing one documentation file is not a code write; it does not break plan mode. State "one file
only, no other writes, no code" so CC does not start implementing.

### 2. Build → small steps, one unit per turn, stop after each
Decompose the implementation into the smallest sensible units (per service, per file, per test) and
run them one CC turn at a time, "do X, stop". Do not say "finish all of Y" that batches into one big turn and wedges. Note: small BUILD steps are not small commits; the commit gate stays atomic per
slice (one slice, one commit, as in _loop.md). Many small build turns still land as one commit.

### 3. Tests → foreground only, with a hard timeout, never backgrounded
Background test plumbing (sleep + cat output, "Invalid tool parameters", retry spin) is a primary
stall source. Always run tests in the foreground: `uv run pytest <path> -q` (or `-v`), and wrap
with `timeout N` so a hanging test self-kills instead of wedging the turn:
`timeout 60 uv run pytest <path> -v`. This is doubly important for anything touching an infinite
loop (run_forever): a hanging loop test is invisible when backgrounded.

### 4. Operator-terminal fallback (the strongest lever)
When CC cannot run bash (the classifier is down) or keeps wedging, the operator runs the command
directly in their own terminal. CC's job is to produce the code (file writes still work); the
operator runs the verification. This bypasses the API entirely for:
- test runs (`timeout N uv run pytest ...`),
- gates (`make lint`, `make test`),
- git (already the operator's job),
- adversarial mutation-kills (see override 6).
The operator's terminal must be the same project environment CC uses (same uv venv, .env loaded,
local stack up) or results will diverge. Rule of thumb: if a step does not need the model to think,
run it in the terminal, not through CC.

### 5. Fresh session resets accumulated context
A long session accumulates context, so even a short instruction late in it carries a large effective
turn. When wedging persists, close the session and start fresh; point the new session at the slice
doc + the scratch plan + `git status --short`. Have it report done-vs-missing FROM the git status and
confirm that itself before building (do not hand it a conclusion; make it verify); this prevents
both redoing done work and skipping partial work. Nothing is lost: CC writes to disk as it goes, so
resume is short because the inputs are pinned.

### 6. Adversarial pass when CC cannot run mutations → operator runs them in-terminal
The adversarial step is not skipped; it is relocated. This is a WEAKER form than CC self-validating
(operator + CHAT are less independent than a separate CC pass), so CHAT must derive each mutation
target from the load-bearing PROPERTY itself, not from CC's own test names. CHAT supplies each
mutation-kill as a terminal recipe the operator runs:
- an edit (one-line `sed`) to break the property,
- `timeout N uv run pytest <the test that should catch it> -v`,
- expected result: that test FAILS (CHAT confirms it failed for the right reason),
- revert, re-run, confirm green.
One mutation at a time; operator pastes each result; CHAT confirms before the next.

## What does NOT change (the invariant)

This overlay changes execution mechanics only. It does not relax:
- the gates (make check / lint / mypy / test must be green before the commit gate),
- the inventory (step 7) and adversarial (step 8) passes still happen, executed via the
  operator's terminal where CC cannot,
- the commit gate (operator assigns the D-number, one atomic commit per slice, fetch-rebase-push),
- load-bearing claims needing inline evidence, mutation-killing the real properties, completion
  reports with itemized inventory.
If applying the overlay would mean skipping verification, do not skip it; move it to the terminal
instead.

## CHAT's responsibilities under this overlay

- Rule out a real failure first, then detect the trigger and propose the switch (operator confirms).
- Break work into small turns / micro-asks proactively; never hand CC a big batched instruction.
- Supply terminal commands (tests, gates, mutation recipes) for the operator to run directly, with
  the expected output stated so the operator knows pass from fail.
- Derive adversarial mutation targets from the properties, not from CC's test names.
- Track on-disk state (via `git status --short`) so a fresh session can resume without redoing work,
  and make the resuming session verify done-vs-missing itself.
- Keep the verification discipline intact by relocating it to the terminal, not dropping it.

## Origin

Captured from the day the Slice 40a (cloud-wiring) build hit sustained 529 / large-turn wedging.
The micro-ask-to-file plan, the small-step build, foreground+timeout tests, the operator-terminal
fallback, and terminal mutation-kills all worked that day; the gates and adversarial discipline held
throughout. Kept as a reusable "work with Claude" toolkit entry for the exceptional days, not a
change to the default loop.
