import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { api, type Me, type UiConfig } from "./api";
import { App } from "./App";
import { ChaosProvider } from "./chaos/ChaosContext";
import { SessionProvider } from "./session";
import "./styles.css";

async function boot() {
  const config: UiConfig = await api.uiConfig();
  let me: Me | null = null;
  try {
    me = await api.me();
  } catch {
    me = null;
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ChaosProvider config={config}>
        <BrowserRouter>
          <SessionProvider initial={me}>
            <App />
          </SessionProvider>
        </BrowserRouter>
      </ChaosProvider>
    </StrictMode>,
  );
}

void boot();
