import { useEffect, useState } from "react"

export type Route =
  | { page: "overview" | "pipeline" | "negotiations" | "evidence" | "final" }
  | { page: "evaluations"; tab: "local" | "hosted" | "prompts" }
  | { page: "replay"; runId: string; from?: "evaluations" | "final" }

// Hash routes stay compatible with links in the README (#prompts, #run/<id>).
function parse(hash: string): Route {
  const h = hash.replace(/^#\/?/, "")
  if (h.startsWith("final/run/"))
    return { page: "replay", runId: decodeURIComponent(h.slice("final/run/".length)), from: "final" }
  if (h.startsWith("evaluations/run/"))
    return { page: "replay", runId: decodeURIComponent(h.slice("evaluations/run/".length)), from: "evaluations" }
  if (h.startsWith("run/"))
    return { page: "replay", runId: decodeURIComponent(h.slice(4)) }
  if (h === "prompts") return { page: "evaluations", tab: "prompts" }
  if (h.startsWith("evaluations")) {
    const tab = h.split("/")[1]
    return {
      page: "evaluations",
      tab:
        tab === "hosted" || tab === "prompts"
          ? tab
          : "local",
    }
  }
  if (h === "negotiations" || h === "evidence" || h === "pipeline" || h === "final")
    return { page: h }
  return { page: "overview" }
}

export function useHashRoute() {
  const [route, setRoute] = useState(() => parse(location.hash))
  useEffect(() => {
    const onChange = () => {
      setRoute(parse(location.hash))
      window.scrollTo(0, 0)
    }
    window.addEventListener("hashchange", onChange)
    return () => window.removeEventListener("hashchange", onChange)
  }, [])
  return route
}

export const go = (hash: string) => {
  location.hash = hash
}
