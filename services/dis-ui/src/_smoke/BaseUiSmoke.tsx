import { Button } from '@base-ui/react/button'
import { Dialog } from '@base-ui/react/dialog'

// Throwaway de-risk artifact (slice 23 Checkpoint 1). NOT wired into routes. Proves
// that @base-ui/react primitives (Button + Dialog: Root/Trigger/Portal/Popup) mount
// and behave under Vite 8 + React 19, and that tw-animate-css utilities (animate-in,
// fade-in-0, zoom-in-95) apply to a Base-UI-state-driven element with Tailwind v4.
// Once the real components/ui primitives exist (Checkpoint 2), this can be removed.
export function BaseUiSmoke() {
  return (
    <div>
      <Button className="rounded-md bg-primary px-2.5 py-1 text-sm font-medium text-primary-foreground">
        Smoke button
      </Button>

      <Dialog.Root>
        <Dialog.Trigger className="rounded-md border border-border px-2.5 py-1 text-sm">
          Open dialog
        </Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 bg-black/10" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 -translate-x-1/2 -translate-y-1/2 rounded-md bg-popover p-4 text-sm text-popover-foreground ring-1 ring-foreground/10 animate-in fade-in-0 zoom-in-95">
            <Dialog.Title className="text-subheading">Smoke dialog</Dialog.Title>
            <Dialog.Description>Base UI mounted and opened under Vite.</Dialog.Description>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
