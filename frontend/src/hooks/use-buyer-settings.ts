import { useState } from "react"

import type { Status } from "@/api"

export type BuyerSelection = { model: string; effort: string }
type Scope = "a" | "b" | "practice"
const storageKey = "aeiou.eval-suite.models.v1"

export function useBuyerSettings(status: Status | undefined) {
  const [saved, setSaved] = useState<Partial<Record<Scope, BuyerSelection>>>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) ?? "{}")
      return Object.fromEntries((["a", "b", "practice"] as const)
        .filter(id => typeof value?.[id]?.model === "string" && typeof value?.[id]?.effort === "string")
        .map(id => [id, value[id]]))
    } catch { return {} }
  })
  const selection = (scope: Scope): BuyerSelection => {
    const current = saved[scope]
    const model = status?.models.find(item => item.id === current?.model)
      ?? status?.models.find(item => item.id === status.model)
    return {
      model: model?.id ?? "claude-sonnet-5-5",
      effort: current && model?.id === current.model && model.efforts.includes(current.effort)
        ? current.effort : model?.default_effort ?? "high",
    }
  }
  const change = (scope: Scope, value: BuyerSelection) => {
    const next = { ...saved, [scope]: value }
    setSaved(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* BuyerSelection still works in this tab. */ }
  }
  const ready = (scope: Scope) => {
    const model = status?.models.find(item => item.id === selection(scope).model)
    return !!model && !!status?.providers[model.provider]?.configured
  }
  return { selection, change, ready }
}

