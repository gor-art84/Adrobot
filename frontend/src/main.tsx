import { useState } from "react";
import { createRoot } from "react-dom/client";
import { CreateCampaign } from "./features/create-campaign/CreateCampaign";
import { StreamEditor } from "./features/stream-editor/StreamEditor";
import "./styles.css";

function App() {
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
      {screen === "editor" ? (
        <StreamEditor initialCampaignId={campaignId} />
      ) : (
        <main className="workspace">
          <CreateCampaign openEditor={(id) => {
            setCampaignId(id);
            setScreen("editor");
          }} />
        </main>
      )}
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
