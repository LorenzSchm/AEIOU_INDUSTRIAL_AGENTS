export const num = (value: number | null | undefined, digits = 0) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-GB", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(value)

export const eur = (value: number | null | undefined, digits = 0) =>
  value == null || !Number.isFinite(value) ? "—" : `€${num(value, digits)}`

export const pct = (value: number | null | undefined) =>
  value == null ? "—" : num(value * 100, 1)
export const human = (value: string | null | undefined) =>
  (value ?? "").replaceAll("_", " ")

export const time = (value: string) =>
  new Date(value).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  })
export const dateTime = (value: string) =>
  new Date(value).toLocaleString("en-GB")
