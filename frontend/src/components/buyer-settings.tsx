import type { Status } from "@/api"
import type { BuyerSelection } from "@/hooks/use-buyer-settings"

export function BuyerSettings({ label, status, value, onChange, disabled }: {
  label: string; status: Status | undefined; value: BuyerSelection; onChange: (value: BuyerSelection) => void; disabled: boolean
}) {
  const model = status?.models.find(item => item.id === value.model)
  const configured = model && status?.providers[model.provider]?.configured
  return <div className="space-y-2">
    <div className="flex flex-wrap gap-3">
      <label className="grid min-w-0 flex-1 gap-1 text-xs text-muted-foreground">Buyer model
        <select aria-label={`${label} model`} value={value.model} disabled={disabled || !status}
          onChange={event => {
            const next = status?.models.find(item => item.id === event.target.value)
            if (next) onChange({ model: next.id, effort: next.default_effort })
          }} className="h-9 w-full rounded-md border bg-background px-3 text-sm text-foreground">
          {status?.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
      <label className="grid gap-1 text-xs text-muted-foreground">Reasoning effort
        <select aria-label={`${label} effort`} value={value.effort} disabled={disabled || !status}
          onChange={event => onChange({ ...value, effort: event.target.value })}
          className="h-9 rounded-md border bg-background px-3 text-sm text-foreground">
          {model?.efforts.map(effort => <option key={effort} value={effort}>{effort === "xhigh" ? "Extra high" : effort[0].toUpperCase() + effort.slice(1)}</option>)}
        </select>
      </label>
    </div>
    {model && !configured && <p role="status" className="text-xs text-amber-700">
      Add {model.provider === "openai" ? "OPENAI_API_KEY" : "ANTHROPIC_API_KEY"} to the local .env file to enable this model.
    </p>}
  </div>
}
