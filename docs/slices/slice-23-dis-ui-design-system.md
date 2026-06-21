# Slice 23: DIS UI design-system adoption (shadcn base-nova, Base UI) to match the admin-frontend

**Status:** TODO.

**Phase:** cross-cutting (UI track). A pure visual and component migration of the existing DIS UI. Builds on slices 20 to 22.

**Owners:** UI track. Visual source: the Sevyn8/ithina-retail-admin-frontend design language (tokens and generic primitive styling only; see the design-only fence below).

---

## Goal

Adopt the admin-frontend's design system in the DIS UI so the two share the same look and feel: the shadcn base-nova style (built on Base UI), its oklch token set, type scale, dense enterprise spacing, the Geist font, light and dark modes with a toggle, and matching generic primitives (button, card, table, input, badge, dialog, and the supporting set). Then rebuild every existing DIS UI screen onto those primitives.

This is a pure visual and component migration. It changes no functionality, no routes, no fixtures, no contracts, and no behavior. The existing test suite is the safety net: it must stay green throughout, with selector updates where markup changes but no change to what any test asserts about behavior.

## Design-only fence (non-negotiable)

The admin-frontend is a separate repo. This slice takes ONLY its look and feel: design tokens and the styling of generic primitives. It mines NO surfaces, no screens, pages, routes, forms, auth, API clients, hooks, stores, or data logic. Generic primitives (a button, a card) are design and come over freely; anything functional does not. We port styling, not behavior.

## Source design language (from the read-only extraction)

- **Stack:** shadcn `base-nova` style on Base UI (`@base-ui/react`), Tailwind v4 CSS-first (already the DIS UI's form, so tokens transplant rather than translate). cva + clsx + tailwind-merge; `tw-animate-css`; lucide icons; `next-themes` for mode.
- **Tokens:** an `@theme inline` block mapping oklch `:root` (light) and `.dark` sets. One blue primary `oklch(0.62 0.19 257)`; achromatic neutral scale; semantic success/warning/danger(=destructive)/info; sidebar and chart token families.
- **Type:** Geist Sans (body and headings, headings by weight), Geist Mono for mono; custom utilities `text-display|heading|subheading|body|body-strong|caption|label|micro`.
- **Radius:** sm 2 / base 4 / md 6 (workhorse) / lg 8 / xl 10 px; badges `rounded-full`.
- **Shadows:** flat; cards border-only; shadows reserved for overlays.
- **Spacing:** dense 4px grid; page p-6, cards p-4/gap-3, table cells p-2 / head h-10, controls h-8, sidebar w-60/w-16, top bar h-14.
- **Primitives (styling):** button (cva variants default/outline/secondary/ghost/destructive/link, sizes xs/sm/default/lg/icon, `active:scale-[0.98]`), badge (pill), card (border-only), input (h-8), table (dense, hover rows), dialog (overlay + animated content).

## Stack-difference risks (resolve in Checkpoint 1)

- **Base UI under Vite.** The admin-frontend is Next 16; the DIS UI is Vite 8 + React 19. `@base-ui/react` is framework-agnostic React and should mount under Vite, but this must be proven before 12 screens depend on it.
- **Geist font.** The admin-frontend loads Geist via `next/font`, which the DIS UI cannot use. Geist must be loaded another way (Fontsource package or self-hosted woff2 with `@font-face` and the `--font-geist-sans` / `--font-geist-mono` variables).
- **Animations.** `tw-animate-css` replaces `tailwindcss-animate`; confirm it works with Tailwind v4 in this project.
- **shadcn CLI.** Its config assumes Next paths; primitives may be brought in manually rather than via the CLI. Either is fine; the result is the same `components/ui` set.

## Hard constraints

1. **No functional change.** Same routes, fixtures, contracts, query keys, auth, and behavior. This slice re-skins and re-components only. If a change would alter behavior, stop and surface it.

2. **Test parity is the proof.** The full suite stays green at every checkpoint. Update selectors and queries where markup changes; do NOT change what a test asserts about behavior. A test that needs a behavioral assertion changed is a signal the rebuild altered function: stop and surface it.

3. **Design-only fence.** Per above. Tokens and generic primitive styling only; no surfaces or logic from the admin-frontend.

4. **Stack additions are scoped to this purpose.** Add only the UI stack the design system needs: `@base-ui/react`, `class-variance-authority`, `clsx`, `tailwind-merge`, `tw-animate-css`, `lucide-react`, `next-themes`, and a Geist font package. No unrelated dependencies.

5. **Tailwind v4 CSS-first preserved.** Tokens live in CSS via `@theme` and `:root`/`.dark`, matching both the DIS UI's current form and the admin-frontend's. No `tailwind.config.js` is introduced.

6. **No backend modifications.** Pure UI. Do not edit any backend code.

7. **Repo hygiene.** No em-dashes in any repo file. Precise naming: DIS, DIS UI, dis-ui-server. Strict TS, no `any`, ESLint and Prettier clean.

8. **Git discipline.** Claude Code does not push; the pre-push hook enforces it; the operator reviews and pushes. No Co-Authored-By trailers. One coherent commit per checkpoint.

## Acceptance criteria

1. The DIS UI carries the admin-frontend token set: the `@theme` block, the oklch `:root` and `.dark` token families (background, foreground, surface, primary, secondary, muted, accent, border, input, ring, success, warning, danger/destructive, info, sidebar, chart), and the type-scale utilities.
2. Geist Sans and Geist Mono load under Vite (not via next/font) and are applied as the sans and mono fonts.
3. Light and dark modes both work via `next-themes` (or an equivalent class-based toggle), with a visible theme toggle in the app shell; the `.dark` class flips the full token set.
4. A `components/ui` set exists with base-nova-styled primitives: at minimum button, card, table, input, badge, dialog, label, select, plus any others the screens require; each matches the source cva variants and visual treatment.
5. Base UI primitives mount and behave correctly under Vite (proven by a smoke test in Checkpoint 1 before the screen rebuild).
6. The app shell is rebuilt to the admin-frontend chrome: a sticky top bar (h-14, with the theme toggle and the existing profile/logout) and a collapsible sidebar (w-60 expanded, w-16 collapsed, sidebar token colors, active-item chrome).
7. All twelve existing surfaces are rebuilt onto the primitives with no behavior change: Tenant Dashboard, Sources index, Sample Upload, Mapping Review, Quarantine console (list/detail/resubmit), Audit, Mapping Versions, Shadow Rollout Review, Notifications, the four state components, and the dev-login.
8. The state primitives (Loading, Empty, Error, PermissionDenied) and the notification bell adopt the design system.
9. `pnpm install`, `pnpm dev`, `pnpm build`, `pnpm test`, `pnpm lint`, and `pnpm tsc --noEmit` strict all pass at every checkpoint; the full test suite stays green (selector updates only, no behavioral assertion changes).
10. No functional regression: the tenant journey and all screen behaviors work exactly as before the slice.
11. Commit history shows the slice landing via coherent per-checkpoint commits on `main`.

## Failure-mode categories

**FM1: Silent behavior change.** The risk here is not invention, it is altering behavior while restyling. Any test whose behavioral assertion would need to change is a stop-and-surface signal. Markup, class, and selector changes are expected; behavioral changes are not.

**FM2: Stack mismatch surfacing late.** Base UI under Vite, Geist without next/font, and tw-animate-css under v4 are proven in Checkpoint 1 before any screen is rebuilt. Do not defer these unknowns into the screen checkpoints.

**FM3: Fence breach.** Only tokens and generic primitive styling come from the admin-frontend. If a primitive cannot be reproduced from its styling alone and seems to need the source's screen or logic context, surface it; do not import a surface.

**FM4: Scope creep into function.** No new features, no route changes, no fixture or contract edits, no query-key changes. If the rebuild tempts a behavioral improvement, note it as a follow-up; do not fold it in.

**FM5: Token drift.** Reproduce the source oklch values exactly. Do not re-pick colors or radii by eye. The tokens are the contract for visual parity.

**FM6: Incremental greenness.** Each checkpoint leaves the suite green and the app runnable. Do not land a checkpoint that breaks screens not yet rebuilt; rebuilt and not-yet-rebuilt screens coexist until the slice completes.

## Plan-mode prompts per checkpoint

Four checkpoints, reconcile-first, plan mode the one stop, operator reviews and pushes, no push by Claude Code.

### Checkpoint 1: Foundation and de-risk gate

**Plan-mode prompt:**

> "Read `docs/skills/sevyn8-workflow/SKILL.md`, this slice doc, the source design language section above, `services/dis-ui/CLAUDE.md`, and your DIS UI's current `src/index.css` (or the file holding the Tailwind `@theme`), `src/main.tsx`, and `package.json`. Also read (read-only, design only) `~/projects/admin-frontend/app/globals.css`, `components.json`, `package.json`, and `lib/utils.ts` for the exact tokens, the cn() helper, and the dependency versions. Do NOT read its screens or logic.
>
> Produce a plan to:
> - bring the `@theme` block and the oklch `:root`/`.dark` token families and the type-scale utilities into the DIS UI's CSS, reproduced exactly (FM5)
> - add the scoped deps (constraint 4) and a `@/lib/utils` cn()
> - load Geist Sans and Geist Mono under Vite (Fontsource or self-hosted woff2 with the `--font-geist-sans`/`--font-geist-mono` variables), since next/font is unavailable
> - wire `next-themes` (or a class-based equivalent) and a theme toggle, with the `.dark` class flipping the token set
> - a Base UI smoke test: a throwaway button and dialog from `@base-ui/react` mount, render, and animate under Vite, plus `tw-animate-css` works with Tailwind v4 (this is the de-risk gate; if any fails, stop and report rather than proceeding)
> - tests: tokens present, dark class flips a token, Geist applied, the smoke primitives mount
>
> Return the file list, the exact token block, the Geist loading approach, the next-themes wiring, and the smoke-test result expectation. FM2: prove Base UI + Geist + tw-animate-css under Vite here, before any primitive or screen work. Do NOT rebuild primitives or screens in this checkpoint. Return the plan and STOP."

### Checkpoint 2: Primitive set and app shell

**Plan-mode prompt:**

> "Building on Checkpoint 1. Read (read-only, design only) the admin-frontend primitives under `~/projects/admin-frontend/components/ui/` for button, card, table, input, badge, dialog, label, select (and any the DIS UI screens will need), and the app shell / sidebar chrome styling. Quote only cva variants and className styling; mine no logic.
>
> Produce a plan to:
> - create `services/dis-ui/src/components/ui/` with base-nova-styled primitives (button, card, table, input, badge, dialog, label, select, plus needed others), matching the source variants and treatment, on `@base-ui/react`
> - rebuild the app shell: the sticky top bar (h-14, theme toggle, the existing profile email/logout) and the collapsible sidebar (w-60/w-16, sidebar tokens, active-item chrome), preserving the current nav items and the isOps gating exactly (no behavior change)
> - tests: each primitive renders its variants; the shell renders, the sidebar collapses, the theme toggle flips mode, nav and isOps gating unchanged
>
> Return the file list, the primitive inventory with their variants, and the shell rebuild. FM1: the shell's nav and gating behavior is unchanged, only its chrome. FM3: primitives from styling only. Do NOT rebuild the screens yet. Return the plan and STOP."

### Checkpoint 3: Rebuild the tenant core screens

**Plan-mode prompt:**

> "Building on Checkpoints 1 to 2. Rebuild these screens onto the `components/ui` primitives with NO behavior change: Tenant Dashboard, Sources index, Sample Upload, Mapping Review. Read each screen's current source and tests first.
>
> Produce a plan to:
> - replace bespoke markup with the primitives (Card, Table, Button, Input, Badge, Dialog, Select) and the type-scale utilities, keeping every fixture call, query key, route, and behavior identical
> - update test selectors/queries to the new markup WITHOUT changing any behavioral assertion (FM1, FM2)
> - apply the dense spacing and semantic colors (success/warning/danger) in place of the current hardcoded color classes
> - confirm each rebuilt screen still passes its existing behavioral tests
>
> Return the file list, the per-screen primitive mapping, and the test-selector changes (explicitly: which are selector-only vs any that look behavioral, which should not exist). FM4: no feature or route changes. Return the plan and STOP."

### Checkpoint 4: Rebuild the remaining screens and primitives

**Plan-mode prompt:**

> "Building on Checkpoints 1 to 3. Rebuild the rest onto the primitives with NO behavior change: Quarantine console (list/detail/resubmit), Audit, Mapping Versions, Shadow Rollout Review, Notifications and the bell, the four state components (Loading/Empty/Error/PermissionDenied), and the dev-login. Read each current source and tests first.
>
> Produce a plan to:
> - rebuild each onto the primitives and type scale, behavior identical, semantic colors for status/severity/failure displays
> - the four state components adopt the design system (they are used everywhere, so this unifies the look)
> - update test selectors without changing behavioral assertions
> - a final full-suite pass and a manual-journey checklist (Dashboard, Upload to Review to approve, Quarantine to detail to resubmit, Audit, Mappings, Shadow promote, Notifications) confirming no regression
>
> Return the file list, the per-screen mapping, the test-selector changes, and the journey checklist. FM1/FM6: full suite green, no behavioral assertion changed, every screen runnable. Return the plan and STOP."

## Out of scope

- Any new screen or feature (the remaining surfaces, Sources CRUD, ops cluster, are later slices, built in this design system once it exists)
- Real dis-ui-server calls (slice 13); contract changes; fixture changes
- Mobile-responsive redesign beyond what the primitives give for free; i18n
- Production deploy, CI, Playwright e2e

## Companion artifacts

- `~/projects/admin-frontend` (the design source; read-only, design only)
- `services/dis-ui/docs/dis-ui-surface-map.md` (screen layouts, still the functional design reference)
- `docs/slices/slice-20-dis-ui-core.md`, `slice-21-...md`, `slice-22-...md` (the screens being restyled)
- `services/dis-ui/CLAUDE.md`

## References

- `docs/build-guide.md` 6.1, 6.4
- `services/dis-ui/CLAUDE.md`, `README.md`

## Carry-forward

- Once this design system is in place, the remaining surfaces are built in it directly: Sources CRUD (UI defines the register-a-source API, conforming to Sanjeev's composite source schema), and the ops cluster (Ops Fleet, DuckDB, cross-tenant Quarantine, Audit search), whose one real policy dependency is cross-tenant ops read (Sanjeev's RLS call).
- Open items pending the batched Sanjeev message (deferred): the frontend-defined API shapes the UI needs him to build (onboarding, quarantine resubmit, shadow, fleet, notifications link), and the genuine policy questions only he answers (RBAC vocabulary D25, GET /me profile call, source registration in schema, cross-tenant ops RLS, the absent contracts/customer-master/). Plus the demand-list illustrative-id sweep.
- Per the division of authority: the UI owns functionality and API shape; Sanjeev owns Auth, RLS, DB schema, and security policy, and the UI conforms.
