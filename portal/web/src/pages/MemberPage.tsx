import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { api, type MemberDetail, type Note } from "../api";
import { useChaos } from "../chaos/ChaosContext";
import { ConfirmDialog, Interstitial } from "../components/Dialogs";
import { Field, Status } from "../components/Field";
import { money, usDate, usDateTime } from "../format";
import { useApi } from "../session";

export function MemberPage() {
  const { memberNumber = "" } = useParams();
  const { config, t } = useChaos();
  const call = useApi();
  const [member, setMember] = useState<MemberDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const detail = await call(() => api.member(memberNumber), setError);
    if (detail) setMember(detail);
  }, [call, memberNumber]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!member) {
    return error ? <Status error={error} /> : <p className="loading">Loading member record...</p>;
  }

  const sections = [
    <AddressSection key="address" member={member} onSaved={setMember} />,
    <ContactSection key="contact" member={member} onSaved={setMember} />,
    <PoliciesSection key="policies" member={member} />,
    <ClaimsSection key="claims" member={member} />,
    <NotesSection key="notes" member={member} onChanged={load} />,
  ];

  return (
    <>
      <Interstitial />
      <h1>
        {t("member.heading")}: {member.firstName} {member.lastName}
      </h1>
      <table className="summary">
        <tbody>
          <tr>
            <th>Member number</th>
            <td>{member.memberNumber}</td>
            <th>Date of birth</th>
            <td>{usDate(member.dateOfBirth)}</td>
            <th>SSN</th>
            <td>***-**-{member.ssnLast4}</td>
          </tr>
        </tbody>
      </table>
      {config.domDrift.layout ? [...sections].reverse() : sections}
    </>
  );
}

function Section({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <section className="section" aria-label={heading}>
      <h2>{heading}</h2>
      {children}
    </section>
  );
}

/** Edit form whose submit button sits above the fields under layout drift. */
function EditForm({ onSubmit, saveLabel, children }: { onSubmit: () => void; saveLabel: string; children: ReactNode }) {
  const { config } = useChaos();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  const button = <button type="submit">{saveLabel}</button>;
  return (
    <form onSubmit={submit}>
      {config.domDrift.layout && <div className="actions">{button}</div>}
      <table className="form">
        <tbody>{children}</tbody>
      </table>
      {!config.domDrift.layout && <div className="actions">{button}</div>}
    </form>
  );
}

function AddressSection({ member, onSaved }: { member: MemberDetail; onSaved: (m: MemberDetail) => void }) {
  const { t } = useChaos();
  const call = useApi();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ street: "", city: "", state: "", postalCode: "" });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const start = () => {
    setForm({ street: member.street, city: member.city, state: member.state, postalCode: member.postalCode });
    setMessage(null);
    setError(null);
    setEditing(true);
  };
  const save = async () => {
    setError(null);
    const updated = await call(() => api.updateAddress(member.memberNumber, form), setError);
    if (updated) {
      onSaved(updated);
      setEditing(false);
      setMessage("Address saved.");
    }
  };

  return (
    <Section heading={t("address.heading")}>
      <Status message={message} error={error} />
      {editing ? (
        <EditForm onSubmit={save} saveLabel={t("address.save")}>
          <Field label="address.street" value={form.street} onChange={(street) => setForm({ ...form, street })} />
          <Field label="address.city" value={form.city} onChange={(city) => setForm({ ...form, city })} />
          <Field label="address.state" value={form.state} onChange={(state) => setForm({ ...form, state })} size={3} />
          <Field
            label="address.postalCode"
            value={form.postalCode}
            onChange={(postalCode) => setForm({ ...form, postalCode })}
            size={10}
          />
        </EditForm>
      ) : (
        <>
          <address>
            {member.street}
            <br />
            {member.city}, {member.state} {member.postalCode}
          </address>
          <button type="button" onClick={start}>
            {t("address.edit")}
          </button>
        </>
      )}
    </Section>
  );
}

function ContactSection({ member, onSaved }: { member: MemberDetail; onSaved: (m: MemberDetail) => void }) {
  const { t } = useChaos();
  const call = useApi();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ email: "", phone: "" });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const start = () => {
    setForm({ email: member.email, phone: member.phone });
    setMessage(null);
    setError(null);
    setEditing(true);
  };
  const save = async () => {
    setError(null);
    const updated = await call(() => api.updateContact(member.memberNumber, form), setError);
    if (updated) {
      onSaved(updated);
      setEditing(false);
      setMessage("Contact details saved.");
    }
  };

  return (
    <Section heading={t("contact.heading")}>
      <Status message={message} error={error} />
      {editing ? (
        <EditForm onSubmit={save} saveLabel={t("contact.save")}>
          <Field label="contact.email" value={form.email} onChange={(email) => setForm({ ...form, email })} size={34} />
          <Field label="contact.phone" value={form.phone} onChange={(phone) => setForm({ ...form, phone })} />
        </EditForm>
      ) : (
        <>
          <table className="summary">
            <tbody>
              <tr>
                <th>{t("contact.email")}</th>
                <td>{member.email}</td>
              </tr>
              <tr>
                <th>{t("contact.phone")}</th>
                <td>{member.phone}</td>
              </tr>
            </tbody>
          </table>
          <button type="button" onClick={start}>
            {t("contact.edit")}
          </button>
        </>
      )}
    </Section>
  );
}

function PoliciesSection({ member }: { member: MemberDetail }) {
  const { t } = useChaos();
  return (
    <Section heading={t("policies.heading")}>
      <table className="grid" aria-label={t("policies.heading")}>
        <thead>
          <tr>
            <th>Policy number</th>
            <th>Product</th>
            <th>Status</th>
            <th>Effective</th>
            <th>Monthly premium</th>
          </tr>
        </thead>
        <tbody>
          {member.policies.map((p) => (
            <tr key={p.policyNumber}>
              <td>{p.policyNumber}</td>
              <td>{p.product}</td>
              <td>{p.status}</td>
              <td>{usDate(p.effectiveDate)}</td>
              <td className="num">{money(p.monthlyPremium)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function ClaimsSection({ member }: { member: MemberDetail }) {
  const { t } = useChaos();
  return (
    <Section heading={t("claims.heading")}>
      {member.claims.length === 0 ? (
        <p>No claims on file.</p>
      ) : (
        <table className="grid" aria-label={t("claims.heading")}>
          <thead>
            <tr>
              <th>Claim number</th>
              <th>Service date</th>
              <th>Provider</th>
              <th>Amount</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {member.claims.map((c) => (
              <tr key={c.claimNumber}>
                <td>
                  <Link to={`/claims/${c.claimNumber}`}>{c.claimNumber}</Link>
                </td>
                <td>{usDate(c.serviceDate)}</td>
                <td>{c.providerName}</td>
                <td className="num">{money(c.amount)}</td>
                <td>{c.status}</td>
                <td>
                  {(c.status === "APPROVED" || c.status === "PAID") && (
                    <Link to={`/reimbursements/new?member=${member.memberNumber}&claim=${c.claimNumber}`}>
                      {t("claims.reimburse")}
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

function NotesSection({ member, onChanged }: { member: MemberDetail; onChanged: () => Promise<void> }) {
  const { t, id } = useChaos();
  const call = useApi();
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<Note | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const note = await call(() => api.addNote(member.memberNumber, draft), setError);
    if (note) {
      setDraft("");
      setMessage("Note added.");
      await onChanged();
    }
  };

  const remove = async (note: Note) => {
    setConfirming(null);
    setError(null);
    const done = await call(async () => {
      await api.deleteNote(note.id);
      return true;
    }, setError);
    if (done) {
      setMessage("Note deleted.");
      await onChanged();
    }
  };

  return (
    <Section heading={t("notes.heading")}>
      <Status message={message} error={error} />
      <table className="grid notes" aria-label={t("notes.heading")}>
        <thead>
          <tr>
            <th>Date</th>
            <th>Author</th>
            <th>Note</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {member.notes.length === 0 && (
            <tr>
              <td colSpan={4}>No notes.</td>
            </tr>
          )}
          {member.notes.map((n) => (
            <tr key={n.id}>
              <td className="nowrap">{usDateTime(n.createdAt)}</td>
              <td>{n.author}</td>
              <td>{n.body}</td>
              <td>
                <button type="button" onClick={() => setConfirming(n)}>
                  {t("notes.delete")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form onSubmit={add} className="note-form">
        <label htmlFor={id("notes.new")}>{t("notes.new")}</label>
        <br />
        <textarea id={id("notes.new")} rows={3} cols={70} value={draft} onChange={(e) => setDraft(e.target.value)} />
        <br />
        <button type="submit">{t("notes.add")}</button>
      </form>
      {confirming && (
        <ConfirmDialog
          title="Delete note?"
          confirmLabel={t("notes.confirmDelete")}
          cancelLabel={t("notes.cancel")}
          onConfirm={() => void remove(confirming)}
          onCancel={() => setConfirming(null)}
        >
          <p>This note will be removed from the member record:</p>
          <blockquote>{confirming.body}</blockquote>
        </ConfirmDialog>
      )}
    </Section>
  );
}
