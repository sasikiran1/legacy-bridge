import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError, SessionLost, type Me } from "./api";

type Session = {
  me: Me | null;
  setMe: (me: Me | null) => void;
  /** Credentials held in memory between sign-in and a verification wall, so the user can retry. */
  pending: { username: string; password: string } | null;
  setPending: (pending: { username: string; password: string } | null) => void;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ initial, children }: { initial: Me | null; children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(initial);
  const [pending, setPending] = useState<Session["pending"]>(null);
  return <SessionContext.Provider value={{ me, setMe, pending, setPending }}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}

/**
 * Runs an API call for a page. A lost session sends the user back to sign-in (remembering where they were);
 * other API errors come back as a message for the page to show.
 */
export function useApi() {
  const { setMe } = useSession();
  const navigate = useNavigate();
  const location = useLocation();

  return useCallback(
    async <T,>(call: () => Promise<T>, onError: (message: string) => void): Promise<T | undefined> => {
      try {
        return await call();
      } catch (error) {
        if (error instanceof SessionLost) {
          setMe(null);
          const next = encodeURIComponent(location.pathname + location.search);
          navigate(`/login?next=${next}${error.expired ? "&expired=1" : ""}`, { replace: true });
          return undefined;
        }
        onError(error instanceof ApiError ? error.message : "The portal could not complete the request.");
        return undefined;
      }
    },
    [navigate, location, setMe],
  );
}
