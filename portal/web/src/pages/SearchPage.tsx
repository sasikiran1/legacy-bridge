import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, type MemberSummary } from "../api";
import { useChaos } from "../chaos/ChaosContext";
import { Status } from "../components/Field";
import { usDate } from "../format";
import { useApi } from "../session";

export function SearchPage() {
  const { t, id } = useChaos();
  const call = useApi();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemberSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const found = await call(() => api.search(query), setError);
    if (found) setResults(found);
  };

  return (
    <>
      <h1>{t("nav.search")}</h1>
      <form className="box" onSubmit={search}>
        <label htmlFor={id("search.query")}>{t("search.query")}</label>{" "}
        <input id={id("search.query")} value={query} onChange={(e) => setQuery(e.target.value)} size={30} />{" "}
        <button type="submit">{t("search.submit")}</button>
      </form>
      <Status error={error} />
      {results && (
        <table className="grid" aria-label="Search results">
          <thead>
            <tr>
              <th>Member number</th>
              <th>Name</th>
              <th>Date of birth</th>
              <th>City</th>
            </tr>
          </thead>
          <tbody>
            {results.length === 0 && (
              <tr>
                <td colSpan={4}>No members found.</td>
              </tr>
            )}
            {results.map((m) => (
              <tr key={m.memberNumber}>
                <td>
                  <Link to={`/members/${m.memberNumber}`}>{m.memberNumber}</Link>
                </td>
                <td>
                  {m.lastName}, {m.firstName}
                </td>
                <td>{usDate(m.dateOfBirth)}</td>
                <td>
                  {m.city}, {m.state}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
