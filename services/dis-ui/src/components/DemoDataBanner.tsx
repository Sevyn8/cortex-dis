import { Info } from 'lucide-react'

// CSV-journey notice (T11). Honest, visible-but-not-alarming: the parse is REAL (your CSV is
// read and profiled in your browser). Mapping suggestions are AI when the server has a model
// key and basic name matching otherwise (the review step labels which). Preview and saving the
// template still arrive with dis-ui-server. Presentation only; light + dark via tokens.
export function DemoDataBanner() {
  return (
    <div
      role="note"
      className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-caption text-muted-foreground"
    >
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>
        Your CSV is parsed and profiled in your browser. Mapping suggestions are AI-assisted when
        available and basic name matching otherwise; preview and saving the template arrive with
        dis-ui-server.
      </span>
    </div>
  )
}
