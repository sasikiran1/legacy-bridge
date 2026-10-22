import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { api } from "./api";
import { useChaos } from "./chaos/ChaosContext";
import { ClaimPage } from "./pages/ClaimPage";
import { LoginPage, SecurityCheckPage } from "./pages/LoginPage";
import { MemberPage } from "./pages/MemberPage";
import { ReimbursementPage } from "./pages/ReimbursementPage";
import { SearchPage } from "./pages/SearchPage";
import { useSession } from "./session";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/security-check" element={<SecurityCheckPage />} />
      <Route path="/" element={<Authenticated><SearchPage /></Authenticated>} />
      <Route path="/members/:memberNumber" element={<Authenticated><MemberPage /></Authenticated>} />
      <Route path="/claims/:claimNumber" element={<Authenticated><ClaimPage /></Authenticated>} />
      <Route path="/reimbursements/new" element={<Authenticated><ReimbursementPage /></Authenticated>} />
      <Route path="*" element={<Authenticated><NotFound /></Authenticated>} />
    </Routes>
  );
}

function Authenticated({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const location = useLocation();
  if (!me) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }
  return <Shell>{children}</Shell>;
}

function Shell({ children }: { children: ReactNode }) {
  const { me, setMe } = useSession();
  const { config, t } = useChaos();
  const navigate = useNavigate();

  const signOut = async () => {
    await api.signOut().catch(() => undefined);
    setMe(null);
    navigate("/login");
  };

  const nav = (
    <nav className="nav" aria-label="Main">
      <Link to="/">{t("nav.search")}</Link>
      <Link to="/reimbursements/new">{t("nav.reimbursement")}</Link>
      <button type="button" className="link-button" onClick={signOut}>
        {t("nav.signOut")}
      </button>
    </nav>
  );

  return (
    <div className="page">
      <header className="banner">
        <div className="banner-title">MEMBER SERVICES PORTAL</div>
        <div className="banner-user">
          Logged in as <b>{me?.displayName}</b> ({me?.username})
        </div>
      </header>
      {config.domDrift.layout ? (
        <div className="with-sidebar">
          <main className="content">{children}</main>
          <aside className="sidebar">{nav}</aside>
        </div>
      ) : (
        <>
          {nav}
          <main className="content">{children}</main>
        </>
      )}
      <footer className="footer">Member Services Portal 4.2.7 &middot; Best viewed at 1024x768</footer>
    </div>
  );
}

function NotFound() {
  return (
    <>
      <h1>Page not found</h1>
      <p>
        The requested page does not exist. Return to <Link to="/">member search</Link>.
      </p>
    </>
  );
}
