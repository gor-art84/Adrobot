const fallbackMessage = "Не удалось выполнить запрос.";

const fieldLabels: Record<string, string> = {
  name: "Название",
  country: "Код страны",
  offer_id: "ID оффера",
  domain_id: "Домен",
  group_id: "Группа",
  traffic_source_id: "Источник",
  enable_after_create: "Включить после создания",
  operation_id: "Идентификатор операции",
  campaign_id: "ID кампании",
  stream_id: "ID потока",
  revision: "Ревизия черновика",
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validationField(location: unknown): string {
  if (!Array.isArray(location) || !location.every((part) =>
    typeof part === "string" || (typeof part === "number" && Number.isInteger(part) && part >= 0)
  )) return "Запрос";

  const parts = [...location] as (string | number)[];
  if (["body", "query", "path", "header", "cookie"].includes(String(parts[0]))) parts.shift();
  return parts.reduce<string>((path, part) => {
    if (typeof part === "number") return `${path}[${part}]`;
    const label = Object.hasOwn(fieldLabels, part) ? fieldLabels[part] : part;
    return path ? `${path}.${label}` : label;
  }, "") || "Запрос";
}

function errorMessage(body: unknown): string {
  const detail = isObject(body) ? body.detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (isObject(detail) && typeof detail.message === "string" && detail.message.trim()) {
    return detail.message;
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((entry) => {
      if (!isObject(entry) || typeof entry.msg !== "string" || !entry.msg.trim()) return [];
      return [`${validationField(entry.loc)}: ${entry.msg.trim()}`];
    });
    if (messages.length) return messages.join("; ");
  }
  return fallbackMessage;
}

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok || body === null) {
    throw new Error(errorMessage(body));
  }
  return body as T;
}
