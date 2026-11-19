import { useState } from "react";
import { CreateCampaign } from "./features/create-campaign/CreateCampaign";
import { StreamEditor } from "./features/stream-editor/StreamEditor";
import "./styles.css";

export function App() {
  const [screen, setScreen] = useState<"editor" | "create">("editor");
  const [campaignId, setCampaignId] = useState<number>();

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="wordmark">Adrobot</span>
          <nav className="app-nav" aria-label="Разделы Adrobot">
            <button aria-current={screen === "editor" ? "page" : undefined} onClick={() => setScreen("editor")}>Редактор потоков</button>
            <button aria-current={screen === "create" ? "page" : undefined} onClick={() => setScreen("create")}>Создать кампанию</button>
          </nav>
        </div>
      </header>
      <div hidden={screen !== "editor"}>
        <StreamEditor initialCampaignId={campaignId} />
      </div>
      <div hidden={screen !== "create"}>
        <main className="workspace">
          <CreateCampaign openEditor={(id) => {
            setCampaignId(id);
            setScreen("editor");
          }} />
        </main>
      </div>
    </>
  );
}
