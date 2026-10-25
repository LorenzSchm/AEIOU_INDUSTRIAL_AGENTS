import { useEffect, useState } from "react"
import { ArrowUpRight, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { go } from "@/hooks/use-hash-route"
import { useWorkbench, type Scope } from "@/store"

const agents = [
  { value: "original", label: "Your prompt · buyer walk-aways disabled" },
  { value: "claude", label: "Earlier rewrite · Claude + economic guards" },
  { value: "adaptive", label: "AEIOU · deterministic adaptive policy" },
  { value: "baseline", label: "Upstream naive baseline" },
]

function Field({
  id,
  label,
  children,
}: {
  id: string
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  )
}

export function LaunchDialog() {
  const { launch, closeLaunch, cases, status, setScope, refresh } =
    useWorkbench()
  const [mode, setMode] = useState<Scope>("local")
  const [caseId, setCaseId] = useState("")
  const [agent, setAgent] = useState("original")
  const [opponent, setOpponent] = useState("auto")
  const [live, setLive] = useState(false)
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const choices =
    mode === "local" ? cases.local : cases.hosted.filter((c) => c.hosted)

  // Reset the form from the preset each time the dialog opens.
  useEffect(() => {
    if (!launch.open) return
    const m = launch.preset.mode ?? "local"
    const list =
      m === "local" ? cases.local : cases.hosted.filter((c) => c.hosted)
    setMode(m)
    setCaseId(
      launch.preset.caseId &&
        list.some((c) => c.case_id === launch.preset.caseId)
        ? launch.preset.caseId
        : (list[0]?.case_id ?? ""),
    )
    setAgent(status?.llm_configured ? "original" : "adaptive")
    setOpponent("auto")
    setLive(!!launch.preset.live)
    setError("")
  }, [launch, cases, status?.llm_configured])

  const changeMode = (m: Scope) => {
    setMode(m)
    const list =
      m === "local" ? cases.local : cases.hosted.filter((c) => c.hosted)
    setCaseId(list[0]?.case_id ?? "")
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError("")
    try {
      // Live pacing slows local exchanges so a demo audience can watch the deal move through the pipeline.
      const body = {
        mode,
        case_id: caseId,
        agent,
        opponent,
        pace: live && mode === "local" ? 1.2 : 0,
      }
      const r = await api<{ id: string }>("/api/play", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      closeLaunch()
      setScope(mode)
      await refresh()
      if (launch.preset.stay) {
        toast.success("Negotiation launched", {
          description: "Watch it move through the pipeline.",
          action: { label: "Open replay", onClick: () => go(`run/${r.id}`) },
        })
      } else {
        toast.success("Negotiation launched", {
          description: "The agent is preparing its first move.",
        })
        go(`run/${r.id}`)
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const note =
    mode === "local"
      ? "Every local supplier uses Claude and consumes Anthropic credits, including tests with a policy buyer. No arena sessions are used."
      : "Buyer calls use your selected model provider. Hosted sessions count against your practice quota."

  return (
    <Dialog open={launch.open} onOpenChange={(o) => !o && closeLaunch()}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <p className="text-xs font-semibold text-orange-700">
              Start a negotiation
            </p>
            <DialogTitle className="text-xl">
              Put your agent to work.
            </DialogTitle>
            <DialogDescription>
              Choose a case and a test environment.
            </DialogDescription>
          </DialogHeader>

          <Field id="launch-mode" label="Environment">
            <Select value={mode} onValueChange={(v) => changeMode(v as Scope)}>
              <SelectTrigger id="launch-mode" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="hosted">
                  Hosted practice · uses arena quota
                </SelectItem>
                <SelectItem value="local">
                  Local simulator · no arena quota
                </SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <Field id="launch-case" label="Negotiation case">
            <Select value={caseId} onValueChange={setCaseId}>
              <SelectTrigger
                id="launch-case"
                className="w-full [&>span]:truncate"
              >
                <SelectValue placeholder="No cases available" />
              </SelectTrigger>
              <SelectContent className="max-h-72">
                {choices.map((c) => (
                  <SelectItem key={c.case_id} value={c.case_id}>
                    <span className="font-mono text-xs text-muted-foreground">
                      {c.case_id}
                    </span>{" "}
                    {c.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field id="launch-agent" label="Buyer agent">
            <Select value={agent} onValueChange={setAgent}>
              <SelectTrigger id="launch-agent" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {agents.map((a) => (
                  <SelectItem key={a.value} value={a.value}>
                    {a.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          {mode === "local" && (
            <Field id="launch-opponent" label="Local opponent">
              <Select value={opponent} onValueChange={setOpponent}>
                <SelectTrigger id="launch-opponent" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Match the case’s supplier company</SelectItem>
                  {Object.entries(cases.opponents).map(([k, v]) => (
                    <SelectItem key={k} value={k}>
                      {v.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}

          {mode === "local" && (
            <label
              htmlFor="launch-live"
              className="flex cursor-pointer items-center justify-between gap-4 rounded-lg border px-3 py-2.5"
            >
              <span className="text-sm">
                <span className="font-medium">Live pacing</span>
                <span className="block text-muted-foreground">
                  About 1 s per exchange, so you can watch the deal move.
                </span>
              </span>
              <Switch
                id="launch-live"
                checked={live}
                onCheckedChange={setLive}
              />
            </label>
          )}

          <p className="rounded-lg border bg-muted/50 px-3 py-2.5 text-sm text-muted-foreground">
            {note}
          </p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeLaunch}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !caseId}>
              {submitting ? <Loader2 className="animate-spin" /> : null}
              Launch negotiation <ArrowUpRight />
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
