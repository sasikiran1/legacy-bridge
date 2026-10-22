import { useState, type ReactNode } from "react";
import { useChaos } from "../chaos/ChaosContext";

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <div className="dialog-title" id="confirm-title">
          {title}
        </div>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">
          <button type="button" onClick={onConfirm}>
            {confirmLabel}
          </button>
          <button type="button" onClick={onCancel}>
            {cancelLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** "What's new" modal shown on each member page load while DOM-drift chaos has interstitials on. */
export function Interstitial() {
  const { config, t } = useChaos();
  const [open, setOpen] = useState(config.domDrift.interstitial);
  if (!open) return null;
  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="interstitial-title">
        <div className="dialog-title" id="interstitial-title">
          Portal update
        </div>
        <div className="dialog-body">
          <p>The member record screen has been reorganised. Some fields and buttons have moved or been renamed.</p>
          <p>Please review changes carefully before saving.</p>
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={() => setOpen(false)}>
            {t("interstitial.continue")}
          </button>
        </div>
      </div>
    </div>
  );
}
