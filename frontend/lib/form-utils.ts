export function dividendYieldFromPercentInput(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  const percent = Number(trimmed);
  if (!Number.isFinite(percent)) {
    throw new Error("Dividend yield must be a number");
  }
  if (percent < 0 || percent > 100) {
    throw new Error("Dividend yield must be between 0% and 100%");
  }
  return percent / 100;
}
