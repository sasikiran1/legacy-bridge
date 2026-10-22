import { createContext, useContext, type ReactNode } from "react";
import type { UiConfig } from "../api";
import variants from "./variants.json";

export type LabelKey = keyof typeof variants.normal;

type Chaos = {
  config: UiConfig;
  /** Visible text for a UI element; DOM-drift chaos swaps in the "drift" vocabulary. */
  t: (key: LabelKey) => string;
  /** Element id; DOM-drift chaos replaces readable ids with opaque ones. */
  id: (key: LabelKey) => string;
};

const ChaosContext = createContext<Chaos | null>(null);

function opaqueId(key: string): string {
  let hash = 5381;
  for (const ch of key) hash = ((hash << 5) + hash + ch.charCodeAt(0)) >>> 0;
  return `c${hash.toString(36)}`;
}

export function ChaosProvider({ config, children }: { config: UiConfig; children: ReactNode }) {
  const drifted = config.domDrift.labels;
  const labels = drifted ? variants.drift : variants.normal;
  const value: Chaos = {
    config,
    t: (key) => labels[key],
    id: (key) => (drifted ? opaqueId(key) : key.replace(".", "-")),
  };
  return <ChaosContext.Provider value={value}>{children}</ChaosContext.Provider>;
}

export function useChaos(): Chaos {
  const chaos = useContext(ChaosContext);
  if (!chaos) throw new Error("useChaos outside ChaosProvider");
  return chaos;
}
