export const MAX_CURSOR = 9223372036854775807n;

const sessionPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const cursorPattern = /^e[1-9][0-9]*$/;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isSessionId(value: unknown): value is string {
  return typeof value === "string" && sessionPattern.test(value);
}

export function isCursor(value: unknown): value is string {
  if (typeof value !== "string" || !cursorPattern.test(value)) {
    return false;
  }
  try {
    return cursorValue(value) <= MAX_CURSOR;
  } catch {
    return false;
  }
}

export function cursorValue(value: string): bigint {
  return BigInt(value.slice(1));
}
