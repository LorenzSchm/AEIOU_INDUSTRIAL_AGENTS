import { BookOpen, Flag, FlaskConical, KanbanSquare, LayoutDashboard, Swords } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api"
import { Logo } from "@/components/logo"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import type { Route } from "@/hooks/use-hash-route"
import { useWorkbench } from "@/store"

const nav = [
  { page: "overview", label: "Overview", icon: LayoutDashboard },
  { page: "final", label: "Live final", icon: Flag },
  { page: "pipeline", label: "Deal pipeline", icon: KanbanSquare },
  { page: "negotiations", label: "Negotiations", icon: Swords },
  { page: "evidence", label: "Evidence library", icon: BookOpen },
  { page: "evaluations", label: "Evaluations", icon: FlaskConical },
] as const

function ModelPicker() {
  const { status, refresh } = useWorkbench()
  const choose = async (model: string) => {
    try {
      await api("/api/settings/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) })
      await refresh()
      toast.success("Model switched", { description: `New negotiations use ${status?.models.find(m => m.id === model)?.label ?? model}.` })
    } catch (e) {
      toast.error((e as Error).message)
    }
  }
  return (
    <div className="grid gap-1.5 rounded-lg border bg-card p-2 group-data-[collapsible=icon]:hidden">
      <div className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
        <span className={`size-1.5 shrink-0 rounded-full ${status?.llm_configured ? "bg-emerald-500" : "bg-amber-500"}`} />
        {status?.provider === "openai" ? "OpenAI" : "Anthropic"} {status?.llm_configured ? "configured" : "key required"}
      </div>
      <Select value={status?.model} onValueChange={choose}>
        <SelectTrigger size="sm" className="w-full bg-card" aria-label="Buyer model">
          <SelectValue placeholder="Checking configuration…" />
        </SelectTrigger>
        <SelectContent>
          {status?.models.map(m => (
            <SelectItem key={m.id} value={m.id}>
              {m.label}
            </SelectItem>
          ))}
          {status && !status.models.some(m => m.id === status.model) && <SelectItem value={status.model}>{status.model}</SelectItem>}
        </SelectContent>
      </Select>
    </div>
  )
}

export function AppSidebar({ route }: { route: Route }) {
  const { runs } = useWorkbench()
  const active = route.page === "replay" ? route.from ?? "negotiations" : route.page
  const running = runs.filter(r => r.status === "running").length
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="gap-0 p-3 group-data-[collapsible=icon]:p-2">
        <div className="flex items-center gap-2.5 group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:gap-2">
          <a href="#overview" className="flex min-w-0 flex-1 items-center gap-2.5" aria-label="AEIOU overview">
            <Logo className="group-data-[collapsible=icon]:size-8" />
            <div className="min-w-0 leading-tight group-data-[collapsible=icon]:hidden">
              <div className="text-[15px] font-semibold tracking-tight text-foreground">AEIOU</div>
              <div className="truncate text-xs text-muted-foreground">Hollerau Pumpenwerke</div>
            </div>
          </a>
          <SidebarTrigger className="shrink-0 text-muted-foreground" />
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Workbench</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {nav.map(n => (
                <SidebarMenuItem key={n.page}>
                  <SidebarMenuButton asChild isActive={active === n.page} tooltip={n.label}>
                    <a href={`#${n.page}`} aria-current={active === n.page ? "page" : undefined}>
                      <n.icon />
                      <span>{n.label}</span>
                    </a>
                  </SidebarMenuButton>
                  {n.page === "negotiations" && <SidebarMenuBadge>{running ? `${running} live` : runs.length}</SidebarMenuBadge>}
                  {n.page === "pipeline" && running > 0 && (
                    <SidebarMenuBadge>
                      <span className="size-1.5 animate-pulse rounded-full bg-primary" />
                    </SidebarMenuBadge>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="p-3">
        <ModelPicker />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
