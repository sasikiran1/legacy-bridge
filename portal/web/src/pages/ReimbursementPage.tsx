import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api, type Reimbursement } from "../api";
import { useChaos, type LabelKey } from "../chaos/ChaosContext";
import { Field, Status } from "../components/Field";
import { money } from "../format";
import { useApi } from "../session";

type Form = {
  memberNumber: string;
  claimNumber: string;
  amount: string;
  payeeName: string;
  bankRouting: string;
  bankAccount: string;
};

const FIELDS: [keyof Form, LabelKey][] = [
  ["memberNumber", "reimb.memberNumber"],
  ["claimNumber", "reimb.claimNumber"],
  ["amount", "reimb.amount"],
  ["payeeName", "reimb.payee"],
  ["bankRouting", "reimb.routing"],
  ["bankAccount", "reimb.account"],
];

export function ReimbursementPage() {
  const { config, t } = useChaos();
  const call = useApi();
  const [params] = useSearchParams();
  const [form, setForm] = useState<Form>({
    memberNumber: params.get("member") ?? "",
    claimNumber: params.get("claim") ?? "",
    amount: "",
    payeeName: "",
    bankRouting: "",
    bankAccount: "",
  });
  const [step, setStep] = useState<"edit" | "review">("edit");
  const [done, setDone] = useState<Reimbursement | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const created = await call(() => api.submitReimbursement({ ...form, amount: form.amount.replace(/[$,]/g, "") }), (m) => {
      setError(m);
      setStep("edit");
    });
    if (created) setDone(created);
  };

  const props = { form, setForm, step, setStep, done, error, submit };
  return (
    <>
      <h1>{t("reimb.heading")}</h1>
      {config.poorA11y ? <PoorlyStructuredForm {...props} /> : <AccessibleForm {...props} />}
    </>
  );
}

type FormProps = {
  form: Form;
  setForm: (form: Form) => void;
  step: "edit" | "review";
  setStep: (step: "edit" | "review") => void;
  done: Reimbursement | null;
  error: string | null;
  submit: () => Promise<void>;
};

function AccessibleForm({ form, setForm, step, setStep, done, error, submit }: FormProps) {
  const { config, t } = useChaos();
  if (done) {
    return <Status message={`Reimbursement submitted. Reference ${done.reference}.`} />;
  }
  if (step === "review") {
    return (
      <div className="box">
        <p>Please review the payment details. Payments cannot be recalled once submitted.</p>
        <table className="summary" aria-label="Review">
          <tbody>
            {FIELDS.map(([key, label]) => (
              <tr key={key}>
                <th>{t(label)}</th>
                <td>{key === "amount" ? money(Number(form.amount.replace(/[$,]/g, ""))) : form[key]}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="actions">
          <button type="button" onClick={() => setStep("edit")}>
            {t("reimb.back")}
          </button>{" "}
          <button type="button" onClick={() => void submit()}>
            {t("reimb.confirm")}
          </button>
        </div>
      </div>
    );
  }
  const review = (event: FormEvent) => {
    event.preventDefault();
    setStep("review");
  };
  const button = <button type="submit">{t("reimb.review")}</button>;
  return (
    <form className="box" onSubmit={review}>
      <Status error={error} />
      {config.domDrift.layout && <div className="actions">{button}</div>}
      <table className="form">
        <tbody>
          {FIELDS.map(([key, label]) => (
            <Field key={key} label={label} value={form[key]} onChange={(v) => setForm({ ...form, [key]: v })} />
          ))}
        </tbody>
      </table>
      {!config.domDrift.layout && <div className="actions">{button}</div>}
    </form>
  );
}

/** Text painted on a canvas: visible to people and vision models, absent from the accessibility tree. */
function CanvasText({ text, width, className, onClick }: { text: string; width: number; className: string; onClick?: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, width, 22);
    if (onClick) {
      ctx.fillStyle = "#d4d0c8";
      ctx.fillRect(0, 0, width, 22);
      ctx.strokeStyle = "#404040";
      ctx.strokeRect(0.5, 0.5, width - 1, 21);
    }
    ctx.fillStyle = "#000";
    ctx.font = "11px Tahoma, Verdana, sans-serif";
    ctx.fillText(text, onClick ? 8 : 0, 15);
  }, [text, width, onClick]);
  return <canvas ref={ref} width={width} height={22} className={className} onClick={onClick} />;
}

/**
 * The deliberately inaccessible variant (poorA11y chaos): unlabeled inputs, canvas-painted captions and
 * canvas "buttons" handled through click events on a div wrapper. Only vision can tell the fields apart.
 */
function PoorlyStructuredForm({ form, setForm, step, setStep, done, error, submit }: FormProps) {
  const { t } = useChaos();
  if (done) {
    return <CanvasText text={`Reimbursement submitted. Reference ${done.reference}.`} width={420} className="pa-caption" />;
  }
  if (step === "review") {
    return (
      <div className="pa-form">
        {FIELDS.map(([key, label]) => (
          <div className="pa-row" key={key}>
            <CanvasText text={`${t(label)}: ${form[key]}`} width={420} className="pa-caption" />
          </div>
        ))}
        <div className="pa-row">
          <CanvasText text={t("reimb.back")} width={90} className="pa-button" onClick={() => setStep("edit")} />
          <CanvasText text={t("reimb.confirm")} width={150} className="pa-button" onClick={() => void submit()} />
        </div>
      </div>
    );
  }
  return (
    <div className="pa-form">
      {error && <div className="pa-error">{error}</div>}
      {FIELDS.map(([key, label]) => (
        <div className="pa-row" key={key}>
          <CanvasText text={t(label)} width={170} className="pa-caption" />
          <input className="pa-input" autoComplete="off" value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
        </div>
      ))}
      <div className="pa-row">
        <CanvasText text={t("reimb.review")} width={150} className="pa-button" onClick={() => setStep("review")} />
      </div>
    </div>
  );
}
