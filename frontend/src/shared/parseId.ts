export function parseId(value: string): number | undefined {
  const text = value.trim();
  if (!/^[0-9]+$/.test(text)) return undefined;
  const id = Number(text);
  return Number.isSafeInteger(id) && id > 0 ? id : undefined;
}
