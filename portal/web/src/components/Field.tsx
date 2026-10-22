import type { InputHTMLAttributes } from "react";
import { useChaos, type LabelKey } from "../chaos/ChaosContext";

type Props = { label: LabelKey; value: string; onChange: (value: string) => void } & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange" | "id"
>;

export function Field({ label, value, onChange, ...rest }: Props) {
  const { t, id } = useChaos();
  return (
    <tr>
      <th>
        <label htmlFor={id(label)}>{t(label)}</label>
      </th>
      <td>
        <input id={id(label)} value={value} onChange={(e) => onChange(e.target.value)} {...rest} />
      </td>
    </tr>
  );
}

export function Status({ message, error }: { message?: string | null; error?: string | null }) {
  if (error) {
    return (
      <div className="msg msg-error" role="alert">
        {error}
      </div>
    );
  }
  if (message) {
    return (
      <div className="msg msg-ok" role="status">
        {message}
      </div>
    );
  }
  return null;
}
