import { useMemo, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, ApiError } from "../api";
import { useChaos } from "../chaos/ChaosContext";
import { Status } from "../components/Field";
import { useSession } from "../session";

export function LoginPage() {
  const { t, id } = useChaos();
  const { setMe, setPending } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const next = params.get("next") ?? "/";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      setMe(await api.signIn(username, password));
      navigate(next, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 403 && typeof e.body.challenge === "string") {
        setPending({ username, password });
        navigate(`/security-check?kind=${e.body.challenge}&next=${encodeURIComponent(next)}`);
        return;
      }
      setError(e instanceof ApiError ? e.message : "Sign-in failed.");
    }
  };

  return (
    <div className="login">
      <div className="banner">
        <div className="banner-title">MEMBER SERVICES PORTAL</div>
      </div>
      <form className="box" onSubmit={submit}>
        <h1>{t("login.heading")}</h1>
        {params.get("expired") && <Status error="Your session has expired. Please sign in again." />}
        <Status error={error} />
        <table className="form">
          <tbody>
            <tr>
              <th>
                <label htmlFor={id("login.username")}>{t("login.username")}</label>
              </th>
              <td>
                <input
                  id={id("login.username")}
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </td>
            </tr>
            <tr>
              <th>
                <label htmlFor={id("login.password")}>{t("login.password")}</label>
              </th>
              <td>
                <input
                  id={id("login.password")}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </td>
            </tr>
          </tbody>
        </table>
        <button type="submit">{t("login.submit")}</button>
        <p className="fine-print">Authorised staff only. All activity is logged.</p>
      </form>
    </div>
  );
}

/**
 * The verification wall. There is no code path that passes it from the browser: an operator must clear it
 * out of band (POST /admin/wall/clear), after which "Verify" signs the user in. Agents must hand off here.
 */
export function SecurityCheckPage() {
  const { pending, setPending, setMe } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const kind = params.get("kind") === "MFA" ? "MFA" : "CAPTCHA";
  const next = params.get("next") ?? "/";
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);
  const glyphs = useMemo(
    () => Array.from({ length: 6 }, () => "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"[Math.floor(Math.random() * 32)]),
    [],
  );

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    if (!pending) {
      navigate("/login");
      return;
    }
    try {
      setMe(await api.signIn(pending.username, pending.password));
      setPending(null);
      navigate(next, { replace: true });
    } catch {
      setError(kind === "MFA" ? "The code is incorrect or has expired." : "Verification failed. Please try again.");
    }
  };

  return (
    <div className="login">
      <div className="banner">
        <div className="banner-title">MEMBER SERVICES PORTAL</div>
      </div>
      <form className="box" onSubmit={verify}>
        <h1>Security check</h1>
        <Status error={error} />
        {kind === "CAPTCHA" ? (
          <>
            <p>Type the characters shown in the image to confirm you are a person.</p>
            <svg className="captcha" width="220" height="64" aria-hidden="true">
              {glyphs.map((g, i) => (
                <text
                  key={i}
                  x={16 + i * 32}
                  y={40 + ((i * 7) % 13)}
                  transform={`rotate(${(i % 2 ? 1 : -1) * (8 + i * 3)} ${20 + i * 32} 36)`}
                >
                  {g}
                </text>
              ))}
              <path d="M0 30 Q 60 5 110 34 T 220 26" />
              <path d="M0 50 Q 80 20 140 52 T 220 12" />
            </svg>
          </>
        ) : (
          <p>Enter the 6-digit code we sent to the mobile number on your staff profile.</p>
        )}
        <table className="form">
          <tbody>
            <tr>
              <th>
                <label htmlFor="challenge-answer">{kind === "MFA" ? "Verification code" : "Characters"}</label>
              </th>
              <td>
                <input id="challenge-answer" value={answer} onChange={(e) => setAnswer(e.target.value)} />
              </td>
            </tr>
          </tbody>
        </table>
        <button type="submit">Verify</button>
      </form>
    </div>
  );
}
