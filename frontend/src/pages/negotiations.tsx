import { Plus } from "lucide-react"

import { CasesTable } from "@/components/cases-table"
import { RunsTable } from "@/components/runs-table"
import { Button } from "@/components/ui/button"
import { PageHeader, ScopeTabs } from "@/components/workbench"
import { RunKpis, useScopedRuns } from "@/pages/overview"
import { useWorkbench } from "@/store"

export function Negotiations() {
  const { openLaunch, scope, cases } = useWorkbench()
  const scoped = useScopedRuns()
  const library = scope === "local" ? cases.local : cases.hosted.filter(c => c.hosted)
  return (
    <>
      <PageHeader
        eyebrow="The negotiation desk"
        title="Every conversation."
        muted="A clear audit trail."
        sub="Follow the agent from the opening quote to a complete agreement."
        actions={
          <Button onClick={() => openLaunch({ mode: "local" })}>
            New negotiation <Plus />
          </Button>
        }
      />
      <ScopeTabs />
      <RunKpis />
      <RunsTable rows={scoped} />
      <CasesTable cases={library} mode={scope} />
    </>
  )
}
