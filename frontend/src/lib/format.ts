// Display helpers. Values stay strings until the last moment; Number() is used only for
// rendering and charts.

export function num(value: string | null | undefined): number {
  if (value === null || value === undefined || value === "") return Number.NaN;
  return Number(value);
}

export function money(value: string | null | undefined, digits = 2): string {
  const n = num(value);
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function price(value: string | null | undefined): string {
  const n = num(value);
  if (Number.isNaN(n)) return "—";
  return n.toFixed(4).replace(/0{1,2}$/, "");
}

export function qty(value: string | null | undefined): string {
  const n = num(value);
  if (Number.isNaN(n)) return "—";
  return Number.isInteger(n) ? n.toString() : n.toFixed(2);
}

export function pct(value: string | null | undefined): string {
  const n = num(value);
  if (Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

export function time(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

export function label(value: string): string {
  return value.replace(/_/g, " ").toLowerCase();
}

export function shortId(value: string, length = 10): string {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}
