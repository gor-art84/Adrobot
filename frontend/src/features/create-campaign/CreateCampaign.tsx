import { useEffect, useRef, useState, type FormEvent } from "react";
import { request } from "../../api/request";
import { parseId } from "../../shared/parseId";

interface Reference {
  id: number;
  name: string;
}

interface Options {
  domains: Reference[];
  groups: Reference[];
  traffic_sources: Reference[];
}

interface CreatedCampaign {
  id: number;
  name: string;
  state: "disabled" | "active";
  geo_stream_id: number;
  offer_stream_id: number;
}

const selects = [
  { field: "domain_id", label: "Домен", options: "domains" },
  { field: "group_id", label: "Группа", options: "groups" },
  { field: "traffic_source_id", label: "Источник", options: "traffic_sources" },
] as const;

export function CreateCampaign({ openEditor }: { openEditor: (id: number) => void }) {
  const [options, setOptions] = useState<Options>();
  const [optionsError, setOptionsError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<{ field: string; message: string }>();
  const [country, setCountry] = useState("");
  const [enableAfterCreate, setEnableAfterCreate] = useState(false);
  const [saving, setSaving] = useState(false);
  const operation = useRef<{ payload: string; id: string } | undefined>(undefined);
  const [created, setCreated] = useState<CreatedCampaign>();
  const missingOptions = options
    ? selects.filter((select) => options[select.options].length === 0)
    : [];

  useEffect(() => {
    const controller = new AbortController();
    request<Options>("/api/campaign-options", { signal: controller.signal })
      .then(setOptions)
      .catch((error: Error) => {
        if (!controller.signal.aborted) setOptionsError(error.message);
      });
    return () => controller.abort();
  }, [attempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!options || missingOptions.length || saving || created) return;
    const form = new FormData(event.currentTarget);
    const offerId = parseId(String(form.get("offer_id")));
    const data = {
      name: String(form.get("name")).trim(),
      country: String(form.get("country")).trim().toUpperCase(),
      offer_id: offerId,
      domain_id: Number(form.get("domain_id")),
      group_id: Number(form.get("group_id")),
      traffic_source_id: Number(form.get("traffic_source_id")),
      enable_after_create: enableAfterCreate,
    };
    setError("");
    const invalid = [
      { field: "name", valid: data.name.length > 0, message: "Введите название кампании." },
      { field: "country", valid: /^[A-Z]{2}$/.test(data.country), message: "Введите две латинские буквы, например MX." },
      { field: "offer_id", valid: offerId !== undefined, message: "Введите положительный целочисленный ID оффера." },
      ...selects.map(({ field, label }) => ({
        field,
        valid: data[field] > 0,
        message: `Выберите значение в поле «${label}».`,
      })),
    ].find((field) => !field.valid);
    if (invalid) {
      setFieldError(invalid);
      const input = event.currentTarget.elements.namedItem(invalid.field);
      if (input instanceof HTMLElement) input.focus();
      return;
    }

    setFieldError(undefined);
    const payload = JSON.stringify(data);
    try {
      const saved = JSON.parse(sessionStorage.getItem("adrobot.creation") ?? "null");
      if (saved?.payload === payload && typeof saved.id === "string") {
        operation.current = saved;
      }
    } catch {
      // The in-memory identifier still protects retries if storage is unavailable.
    }
    if (operation.current?.payload !== payload) {
      operation.current = { payload, id: crypto.randomUUID() };
    }
    try {
      sessionStorage.setItem("adrobot.creation", JSON.stringify(operation.current));
    } catch {
      // Switching sections preserves the mounted form and its identifier.
    }
    setSaving(true);
    try {
      setCreated(await request<CreatedCampaign>("/api/campaigns", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...data, operation_id: operation.current.id }),
      }));
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось создать кампанию.");
    } finally {
      setSaving(false);
    }
  }

  function fieldState(field: string) {
    return {
      "aria-invalid": fieldError?.field === field || undefined,
      "aria-describedby": fieldError?.field === field ? `create-${field}-error` : undefined,
    };
  }

  function renderError(field: string) {
    if (fieldError?.field !== field) return null;
    return <p className="field-error" role="alert" id={`create-${field}-error`}>{fieldError.message}</p>;
  }

  return (
    <section className="create-campaign" aria-labelledby="create-heading">
      <h1 id="create-heading">Создание кампании</h1>
      {!options && !optionsError && <p role="status">Загружаем справочники Keitaro…</p>}
      {(optionsError || missingOptions.length > 0) && (
        <>
          <p className="notice notice-error" role="alert">
            {optionsError || `В справочниках Keitaro нет доступных значений: ${missingOptions.map((option) => option.label.toLowerCase()).join(", ")}.`}
          </p>
          <button className="button" onClick={() => {
            setOptions(undefined);
            setOptionsError("");
            setAttempt((value) => value + 1);
          }}>Повторить загрузку</button>
        </>
      )}
      <form
        className="create-form"
        noValidate
        onSubmit={submit}
        onChange={(event) => {
          const input = event.target;
          if ((input instanceof HTMLInputElement || input instanceof HTMLSelectElement) && input.name === fieldError?.field) {
            setFieldError(undefined);
          }
        }}
      >
        <fieldset disabled={!options || saving || !!created}>
          <legend>Настройки кампании</legend>
          <div className="form-fields">
            <div className="form-field field-wide">
              <label className="field-label" htmlFor="create-name">Название</label>
              <input id="create-name" name="name" autoComplete="off" required maxLength={128} {...fieldState("name")} />
              {renderError("name")}
            </div>
            <div className="form-field">
              <label className="field-label" htmlFor="create-country">Код страны</label>
              <input
                id="create-country"
                name="country"
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                required
                placeholder="MX"
                value={country}
                onChange={(event) => setCountry(event.currentTarget.value)}
                onBlur={() => setCountry((value) => value.trim().toUpperCase())}
                {...fieldState("country")}
              />
              {renderError("country")}
            </div>
            <div className="form-field">
              <label className="field-label" htmlFor="create-offer-id">ID оффера</label>
              <input id="create-offer-id" name="offer_id" autoComplete="off" required inputMode="numeric" {...fieldState("offer_id")} />
              {renderError("offer_id")}
            </div>
            {selects.map((select) => (
              <div className="form-field reference-field" key={select.field}>
                <label className="field-label" htmlFor={`create-${select.field}`}>{select.label}</label>
                <div className="select-control">
                  <select id={`create-${select.field}`} name={select.field} defaultValue="" required {...fieldState(select.field)}>
                    <option value="" disabled>Выберите значение</option>
                    {options?.[select.options].map((option) => (
                      <option key={option.id} value={option.id}>{option.name}</option>
                    ))}
                  </select>
                </div>
                {renderError(select.field)}
              </div>
            ))}
          </div>
          <div className="form-footer">
            <label className="campaign-switch">
              <input
                type="checkbox"
                role="switch"
                checked={enableAfterCreate}
                onChange={(event) => setEnableAfterCreate(event.currentTarget.checked)}
                aria-describedby="enable-campaign-hint"
              />
              <span>
                <span className="field-label">Включить после создания</span>
                <span className="switch-hint" id="enable-campaign-hint">После проверки обоих потоков</span>
              </span>
            </label>
            <button className="button button-primary" disabled={missingOptions.length > 0}>
              {saving ? "Создаём кампанию…" : enableAfterCreate ? "Создать и включить" : "Создать выключенную кампанию"}
            </button>
          </div>
        </fieldset>
      </form>
      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {created && (
        <div className="creation-result">
          <p role="status">Кампания {created.id} «{created.name}» создана и {created.state === "active" ? "включена" : "выключена"}.</p>
          <p>GEO-поток {created.geo_stream_id}. Замыкающий поток {created.offer_stream_id}.</p>
          <button className="button" onClick={() => openEditor(created.id)}>Открыть редактор</button>
        </div>
      )}
    </section>
  );
}
