// Small display formatters shared by the panels and the run trace.

const integer = new Intl.NumberFormat("en-US");

export function formatInt(value: number | undefined): string {
  return value === undefined ? "0" : integer.format(value);
}

export function formatTokens(value: number | undefined): string {
  if (value === undefined) {
    return "-";
  }
  if (value < 10_000) {
    return integer.format(value);
  }
  return `${(value / 1000).toFixed(1)}k`;
}

export function formatSeconds(value: number | undefined): string {
  if (value === undefined) {
    return "-";
  }
  if (value < 60) {
    return `${value.toFixed(1)}s`;
  }
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes}m ${seconds}s`;
}
