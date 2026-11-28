export function isDagsterControlTarget(value: unknown): value is string {
  return typeof value === 'string' &&
    (value === 'kor-travel-dagster-daemon' || /^[a-z0-9-]{1,80}-dagster-code-server$/.test(value));
}
