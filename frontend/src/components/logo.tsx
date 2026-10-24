import { useId } from "react"

import { cn } from "@/lib/utils"

// Dark tile with a teal glow top-right, lavender bottom-left and a white serif glyph.
export function Logo({ className }: { className?: string }) {
  const id = useId()
  return (
    <svg viewBox="0 0 40 40" className={cn("size-9 shrink-0", className)} role="img" aria-label="AEIOU">
      <defs>
        <radialGradient id={`${id}-teal`} cx="1" cy="0" r="0.85">
          <stop offset="0" stopColor="#b6f4f1" />
          <stop offset="0.3" stopColor="#4c9fa2" stopOpacity="0.7" />
          <stop offset="0.75" stopColor="#24494d" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${id}-lilac`} cx="0" cy="1" r="0.95">
          <stop offset="0" stopColor="#c2b2e4" />
          <stop offset="0.35" stopColor="#7b6ba6" stopOpacity="0.7" />
          <stop offset="0.8" stopColor="#3b3352" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="40" height="40" rx="9" fill="#14141c" />
      <rect width="40" height="40" rx="9" fill={`url(#${id}-lilac)`} />
      <rect width="40" height="40" rx="9" fill={`url(#${id}-teal)`} />
      <text x="20" y="30.5" textAnchor="middle" fill="#fff" fontSize="28" fontFamily="'Times New Roman', Georgia, serif">
        A
      </text>
    </svg>
  )
}
