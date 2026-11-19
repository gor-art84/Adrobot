import { useEffect, useState, type FormEvent } from "react";
import { request } from "../../api/request";
import { parseId } from "../../shared/parseId";

interface DraftOffer {
  offer_id: number;
  name: string | null;
  share: number;
  state: string;
  pinned: boolean;
  removed: boolean;
}

interface Stream {
  id: number;
  name: string;
  editable: boolean;
  dirty: boolean;
  conflict: boolean;
  revision: number;
  offers: DraftOffer[];
}

type Action =
  | { action: "cancel" | "push" }
  | { action: "add" | "remove" | "restore" | "pin"; offer_id: number };

function StreamCard({
  stream,
  loading,
  onAction,
}: {
  stream: Stream;
  loading: boolean;
  onAction: (action: Action) => Promise<boolean>;
}) {
  const [offerId, setOfferId] = useState("");
  const [offerError, setOfferError] = useState(false);

  async function addOffer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
    const id = parseId(offerId);
    if (id === undefined) {
      setOfferError(true);
      const field = event.currentTarget.elements.namedItem(`offer-${stream.id}`);
      if (field instanceof HTMLInputElement) field.focus();
      return;
    }
    if (await onAction({ action: "add", offer_id: id })) setOfferId("");
  }

  return (
    <article className="stream" data-dirty={stream.dirty}>
      <header className="stream-header">
        <h2>{stream.name}</h2>
        {stream.dirty && <span className="draft-label" role="status">Черновик</span>}
      </header>
      {stream.conflict && <p className="notice notice-warning" role="status">Офферы в Keitaro изменились после создания черновика.</p>}
      {stream.offers.length > 0 && (
        <div className="offer-table" role="region" aria-label={`Офферы потока ${stream.name}`} tabIndex={0}>
          <table>
            <thead>
              <tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col">Статус</th><th scope="col">Действия</th></tr>
            </thead>
            <tbody>
              {stream.offers.map((offer) => (
                <tr key={offer.offer_id} data-removed={offer.removed}>
                  <td><span className="offer-id">{offer.offer_id}</span>{offer.name && <span className="offer-name">{offer.name}</span>}</td>
                  <td className="offer-share">{offer.share}%</td>
                  <td>{offer.removed ? "Снят" : offer.state === "active" ? "Активен" : "Выключен"}</td>
                  <td>
                    <div className="row-actions">
                      <button
                        className={offer.removed ? "button button-small" : "button button-small button-icon button-quiet button-remove"}
                        aria-label={offer.removed ? undefined : "Remove"}
                        title={offer.removed ? undefined : "Снять оффер"}
                        disabled={loading || !stream.editable}
                        onClick={() => void onAction({
                          action: offer.removed ? "restore" : "remove",
                          offer_id: offer.offer_id,
                        })}
                      >
                        {offer.removed ? "Bring back" : (
                          <svg aria-hidden="true" viewBox="0 0 24 24">
                            <path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" />
                          </svg>
                        )}
                      </button>
                      {!offer.removed && offer.state === "active" && (
                        <button
                          className="button button-small button-icon button-quiet"
                          aria-label="Pin"
                          title={offer.pinned ? "Снять закрепление" : "Закрепить долю"}
                          aria-pressed={offer.pinned}
                          disabled={loading || !stream.editable}
                          onClick={() => void onAction({ action: "pin", offer_id: offer.offer_id })}
                        >
                          <svg aria-hidden="true" viewBox="0 0 24 24">
                            <path d="M16 3l5 5-3 1-3 4v4l-3 1-6-6 1-3h4l4-3 1-3zM9 15l-6 6" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {stream.editable && (
        <form className="add-offer" noValidate onSubmit={addOffer}>
          <label className="field-label" htmlFor={`offer-${stream.id}`}>ID оффера</label>
          <div className="field-action">
            <input
              id={`offer-${stream.id}`}
              value={offerId}
              onChange={(event) => {
                setOfferId(event.target.value);
                setOfferError(false);
              }}
              inputMode="numeric"
              autoComplete="off"
              required
              readOnly={loading}
              aria-invalid={offerError || undefined}
              aria-describedby={offerError ? `offer-error-${stream.id}` : undefined}
            />
            <button className="button" disabled={loading}>Add</button>
          </div>
          {offerError && <p className="field-error" id={`offer-error-${stream.id}`} role="alert">Введите положительный целочисленный ID оффера.</p>}
        </form>
      )}
      {stream.dirty && (
        <div className="stream-footer">
          <button className="button button-primary" disabled={loading || !stream.editable} onClick={() => void onAction({ action: "push" })}>
            Push to Keitaro
          </button>
          <button className="button" disabled={loading} onClick={() => void onAction({ action: "cancel" })}>
            Cancel
          </button>
        </div>
      )}
    </article>
  );
}

export function StreamEditor({ initialCampaignId }: { initialCampaignId?: number }) {
  const [campaignId, setCampaignId] = useState(initialCampaignId?.toString() ?? "");
  const [result, setResult] = useState<{ campaignId: number; streams: Stream[] }>();
  const [error, setError] = useState("");
  const [invalidId, setInvalidId] = useState(false);
  const [pending, setPending] = useState<"fetch" | "edit">();
  const loading = pending !== undefined;

  useEffect(() => {
    if (initialCampaignId !== undefined) setCampaignId(String(initialCampaignId));
  }, [initialCampaignId]);

  async function loadStreams(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
    const id = parseId(campaignId);
    if (id === undefined) {
      setInvalidId(true);
      const field = event.currentTarget.elements.namedItem("campaign-id");
      if (field instanceof HTMLInputElement) field.focus();
      return;
    }

    setCampaignId(String(id));
    setPending("fetch");
    setError("");
    try {
      await request<unknown>(`/api/campaigns/${id}/streams`);
      const streams = await request<Stream[]>(`/api/campaigns/${id}/drafts`);
      setResult({ campaignId: id, streams });
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось загрузить потоки.");
    } finally {
      setPending(undefined);
    }
  }

  async function editStream(stream: Stream, action: Action) {
    if (!result || loading) return false;
    const { campaignId, streams } = result;
    setPending("edit");
    setError("");
    try {
      const operation = action.action === "push" ? "push" : "draft";
      const updated = await request<Stream>(`/api/campaigns/${campaignId}/streams/${stream.id}/${operation}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(action.action === "push" ? {} : action),
          revision: stream.revision,
        }),
      });
      setResult({ campaignId, streams: streams.map((row) => row.id === updated.id ? updated : row) });
      return true;
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось изменить черновик.");
      return false;
    } finally {
      setPending(undefined);
    }
  }

  return (
    <main className="workspace">
      <h1>Потоки кампании</h1>
      <form className="campaign-toolbar" noValidate onSubmit={loadStreams}>
        <label className="field-label" htmlFor="campaign-id">ID кампании</label>
        <div className="field-action">
          <input
            id="campaign-id"
            inputMode="numeric"
            autoComplete="off"
            required
            readOnly={loading}
            value={campaignId}
            onChange={(event) => {
              setCampaignId(event.target.value);
              setInvalidId(false);
            }}
            aria-invalid={invalidId || undefined}
            aria-describedby={invalidId ? "campaign-id-error" : undefined}
          />
          <button className="button button-primary" disabled={loading}>
            {pending === "fetch" ? "Загружаем…" : "Fetch Streams"}
          </button>
        </div>
        {invalidId && <p className="field-error" id="campaign-id-error" role="alert">Введите положительный целочисленный ID кампании.</p>}
      </form>
      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {result && (
        <section className="stream-list" aria-label={`Кампания ${result.campaignId}`}>
          <p className="campaign-caption">Кампания <span>#{result.campaignId}</span></p>
          {result.streams.length === 0 && <p className="notice">У кампании нет потоков.</p>}
          {result.streams.map((stream) => (
            <StreamCard
              key={`${result.campaignId}:${stream.id}`}
              stream={stream}
              loading={loading}
              onAction={(action) => editStream(stream, action)}
            />
          ))}
        </section>
      )}
    </main>
  );
}
