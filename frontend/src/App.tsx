import { AppSidebar } from "@/components/app-sidebar"
import { LaunchDialog } from "@/components/launch-dialog"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Loading } from "@/components/workbench"
import { useHashRoute, type Route } from "@/hooks/use-hash-route"
import { Evaluations } from "@/pages/evaluations"
import { Evidence } from "@/pages/evidence"
import { Negotiations } from "@/pages/negotiations"
import { Overview } from "@/pages/overview"
import { Pipeline } from "@/pages/pipeline"
import { FinalRunnerBanner, LiveFinal } from "@/pages/live-final"
import { Replay } from "@/pages/replay"
import { useWorkbench, WorkbenchProvider } from "@/store"

const titles: Record<Route["page"], string> = {
  overview: "Overview",
  final: "Live final",
  pipeline: "Deal pipeline",
  negotiations: "Negotiations",
  evidence: "Evidence library",
  evaluations: "Evaluations",
  replay: "Conversation",
}

function Page({ route }: { route: Route }) {
  const { ready, error } = useWorkbench()
  if (error && !ready)
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
        Could not load the workbench: {error}
        <div className="mt-3">
          <Button variant="outline" size="sm" onClick={() => location.reload()}>
            Retry
          </Button>
        </div>
      </div>
    )
  if (!ready) return <Loading>Loading your negotiation workbench…</Loading>
  switch (route.page) {
    case "final":
      return <LiveFinal />
    case "pipeline":
      return <Pipeline />
    case "negotiations":
      return <Negotiations />
    case "evidence":
      return <Evidence />
    case "evaluations":
      return <Evaluations />
    case "replay":
      return <Replay key={route.runId} runId={route.runId} from={route.from} />
    default:
      return <Overview />
  }
}

function Shell() {
  const route = useHashRoute()
  return (
    <SidebarProvider>
      <AppSidebar route={route} />
      <SidebarInset className="min-w-0 bg-background">
        <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b bg-card/95 px-6 backdrop-blur">
          {/* On desktop the toggle lives in the sidebar header; on phones the sidebar is a drawer. */}
          <SidebarTrigger className="-ml-2 md:hidden" />
          <Separator orientation="vertical" className="h-4 md:hidden" />
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbLink href="#overview">Workspace</BreadcrumbLink>
              </BreadcrumbItem>
              {route.page === "replay" && (
                <>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem>
                    <BreadcrumbLink href={`#${route.from ?? "negotiations"}`}>
                      {route.from === "final" ? "Live final" : route.from === "evaluations" ? "Evaluations" : "Negotiations"}
                    </BreadcrumbLink>
                  </BreadcrumbItem>
                </>
              )}
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbPage>{titles[route.page]}</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
          <Badge variant="outline" className="ml-auto gap-1.5 rounded-md font-medium">
            <span className="size-1.5 rounded-full bg-emerald-500" /> Local workbench
          </Badge>
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 space-y-5 p-6">
          {route.page !== "final" && <FinalRunnerBanner />}
          <Page route={route} />
        </main>
        <footer className="mx-6 flex justify-between border-t py-4 text-xs text-muted-foreground">
          <span>AEIOU · Evidence-led industrial agents</span>
          <span className="hidden sm:inline">Claude & OpenAI · Built for Tacto Arena</span>
        </footer>
      </SidebarInset>
      <LaunchDialog />
      <Toaster position="bottom-right" />
    </SidebarProvider>
  )
}

export default function App() {
  return (
    <WorkbenchProvider>
      <TooltipProvider delayDuration={200}>
        <Shell />
      </TooltipProvider>
    </WorkbenchProvider>
  )
}
