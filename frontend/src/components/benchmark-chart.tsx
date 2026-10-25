import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import type { Benchmark } from "@/api"
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Empty } from "@/components/workbench"

const config = {
  baseline: { label: "Naive baseline", color: "var(--chart-2)" },
  score: { label: "AEIOU", color: "var(--chart-1)" },
} satisfies ChartConfig

// Axis labels stay short so six profiles fit side by side; the tooltip has the full figures.
const short: Record<string, string> = {
  hardliner: "Hardliner",
  cashflow: "Cash flow",
  relationship: "Family",
  index_skeptic: "Evidence",
  adversarial: "Adversarial",
}

export function BenchmarkChart({ report, height = 220 }: { report?: Benchmark; height?: number }) {
  if (!report?.opponents.length) return <Empty>Run a local evaluation to compare against the baseline.</Empty>
  const data = report.opponents.map(o => ({
    name: short[o.id] ?? o.name.replace(/^The /, "").split(" ")[0],
    score: Math.round(o.score * 1000) / 10,
    baseline: Math.round(o.baseline_score * 1000) / 10,
  }))
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <BarChart data={data} barGap={3} accessibilityLayer>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="name" tickLine={false} axisLine={false} tickMargin={8} fontSize={11} interval={0} />
        <YAxis domain={[0, 100]} tickLine={false} axisLine={false} width={28} fontSize={11} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="baseline" fill="var(--color-baseline)" radius={3} />
        <Bar dataKey="score" fill="var(--color-score)" radius={3} />
      </BarChart>
    </ChartContainer>
  )
}
