import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, type ClaimDetail } from "../api";
import { useChaos } from "../chaos/ChaosContext";
import { Status } from "../components/Field";
import { money, usDate } from "../format";
import { useApi } from "../session";

export function ClaimPage() {
  const { claimNumber = "" } = useParams();
  const { t } = useChaos();
  const call = useApi();
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void call(() => api.claim(claimNumber), setError).then((d) => d && setDetail(d));
  }, [call, claimNumber]);

  if (!detail) {
    return error ? <Status error={error} /> : <p className="loading">Loading claim...</p>;
  }
  const { claim, member } = detail;

  const reconsider = async () => {
    setError(null);
    const updated = await call(() => api.requestReconsideration(claim.claimNumber), setError);
    if (updated) {
      setDetail({ claim: updated, member });
      setMessage("Reconsideration requested. The claims team will review this decision.");
    }
  };

  return (
    <>
      <h1>
        {t("claim.heading")}: {claim.claimNumber}
      </h1>
      <Status message={message} error={error} />
      <dl className="detail">
        <dt>Member</dt>
        <dd>
          <Link to={`/members/${member.memberNumber}`}>
            {member.firstName} {member.lastName} ({member.memberNumber})
          </Link>
        </dd>
        <dt>Policy number</dt>
        <dd>{claim.policyNumber}</dd>
        <dt>Service date</dt>
        <dd>{usDate(claim.serviceDate)}</dd>
        <dt>Provider</dt>
        <dd>{claim.providerName}</dd>
        <dt>Billed amount</dt>
        <dd>{money(claim.amount)}</dd>
        <dt>{t("claim.status")}</dt>
        <dd>{claim.status}</dd>
        <dt>Reconsideration</dt>
        <dd>{claim.reconsiderationRequested ? "Requested" : "Not requested"}</dd>
      </dl>
      {claim.status === "DENIED" && !claim.reconsiderationRequested && (
        <button type="button" onClick={reconsider}>
          {t("claim.reconsider")}
        </button>
      )}
    </>
  );
}
