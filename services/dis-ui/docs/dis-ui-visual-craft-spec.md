# DIS UI visual craft spec (slice 23)

**Status:** approved direction, 2026-06-03. The quality bar the slice 23 screen rebuilds (Checkpoints 2 to 4) target.

## What this is, and how it relates to the other references

Three references govern the DIS UI's appearance, each authoritative for a different thing:
- **The surface map** (`dis-ui-surface-map.md`): what each screen contains and does (layout, fields, actions). Unchanged.
- **The admin-frontend design system** (`~/projects/admin-frontend`): the tokens, primitives, fonts, and palette. The base we adopt (shadcn base-nova, oklch tokens, Geist).
- **This spec:** the craft bar for how screens are composed from those primitives.

Where this spec and "exact parity with the admin-frontend" differ, this spec wins. The admin-frontend's own screens are deliberately flat and utilitarian; the DIS UI uses the same design system but composes it with more craft. We adopt its system; we do not copy its screen-level flatness.

This is a visual and composition spec only. It changes no behavior, routes, fixtures, or contracts (slice 23 constraint 1 still holds).

## The bar (what "professional, not bland" means here)

The current screens are unstyled: raw button text, native file inputs, naked tables, colored text for status, no containment, thin spacing. The rebuild fixes each of these with a specific treatment.

### Buttons
- The primary action on a screen is a filled button: solid accent background (the primary token), readable foreground, medium weight, optional leading icon, and a subtle press state. Never a bare-text or hairline-border control for a primary action (this is the "Analyze sample" problem).
- Secondary actions use the outline or ghost variant. Destructive uses the destructive variant. Disabled actions (for example "New version (Phase 2)") render visibly disabled, not as plain dimmed text.
- Use the base-nova Button primitive and its variants; do not hand-roll buttons.

### File upload
- Replace the native file input ("Choose File / No file chosen") with a styled dropzone: a bordered (dashed) drop area, an upload icon, a clear "drag and drop or browse" prompt, and the constraint as a hint ("CSV up to 10 MB"). It is the hero element of the Upload screen and should look like one.

### Tables
- Tables sit inside a card or a bordered surface, never floating on the page background.
- Header row has a subtle fill and a clear bottom border; body rows have a hover state and consistent cell padding (the dense base-nova table treatment).
- Status, state, and category columns render as badges (see below), not colored or plain text.
- Long identifier values (trace_id, source_id) use the mono font and may truncate with a copy affordance.

### Status as badges
- Every status, severity, health, and state value renders as a Badge pill with a semantic variant, never as colored or bare text. This covers: dashboard healthy/warning/failing; mapping ACTIVE/STAGED/DEPRECATED; quarantine failure_stage; notification info/warning/error severity.
- Map to semantic tokens: success (green), warning (amber), danger (red), info (blue/violet), neutral (muted) for inert states like deprecated.

### Forms
- Every field has a label (12 to 13px, medium weight, muted) above the control, with consistent vertical rhythm between fields.
- Group a form's fields inside a card or a bordered section, not stacked bare against the page edge
