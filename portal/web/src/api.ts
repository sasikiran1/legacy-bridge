export type DomDrift = { labels: boolean; layout: boolean; interstitial: boolean };
export type Wall = "NONE" | "CAPTCHA" | "MFA";
export type UiConfig = { domDrift: DomDrift; poorA11y: boolean; wall: Wall };
export type Me = { username: string; displayName: string; role: string };

export type MemberSummary = {
  memberNumber: string;
  firstName: string;
  lastName: string;
  dateOfBirth: string;
  city: string;
  state: string;
};

export type Policy = {
  policyNumber: string;
  product: string;
  status: string;
  effectiveDate: string;
  monthlyPremium: number;
};

export type Claim = {
  claimNumber: string;
  policyNumber: string;
  memberNumber: string;
  serviceDate: string;
  providerName: string;
  amount: number;
  status: "RECEIVED" | "IN_REVIEW" | "APPROVED" | "DENIED" | "PAID";
  reconsiderationRequested: boolean;
};

export type Note = { id: number; author: string; body: string; createdAt: string };

export type MemberDetail = {
  memberNumber: string;
  firstName: string;
  lastName: string;
  dateOfBirth: string;
  ssnLast4: string;
  email: string;
  phone: string;
  street: string;
  city: string;
  state: string;
  postalCode: string;
  policies: Policy[];
  claims: Claim[];
  notes: Note[];
};

export type ClaimDetail = { claim: Claim; member: MemberSummary };

export type Reimbursement = { reference: string; status: string; submittedAt: string };

export class ApiError extends Error {
  readonly status: number;
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    super(typeof body.error === "string" ? body.error : `Request failed (${status})`);
    this.status = status;
    this.body = body;
  }
}

/** Raised for 401s outside the sign-in flow; the app catches it and returns to the sign-in page. */
export class SessionLost extends Error {
  readonly expired: boolean;

  constructor(expired: boolean) {
    super(expired ? "Your session has expired." : "Please sign in.");
    this.expired = expired;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  if (response.status === 401 && path !== "/internal/session") {
    throw new SessionLost(parsed.error === "session_expired");
  }
  if (!response.ok) {
    throw new ApiError(response.status, parsed);
  }
  return parsed as T;
}

export const api = {
  uiConfig: () => request<UiConfig>("GET", "/internal/ui-config"),
  me: () => request<Me>("GET", "/internal/session"),
  signIn: (username: string, password: string) =>
    request<Me>("POST", "/internal/session", { username, password }),
  signOut: () => request<void>("DELETE", "/internal/session"),
  search: (q: string) => request<MemberSummary[]>("GET", `/internal/members?q=${encodeURIComponent(q)}`),
  member: (memberNumber: string) => request<MemberDetail>("GET", `/internal/members/${memberNumber}`),
  updateAddress: (memberNumber: string, address: Record<string, string>) =>
    request<MemberDetail>("PUT", `/internal/members/${memberNumber}/address`, address),
  updateContact: (memberNumber: string, contact: Record<string, string>) =>
    request<MemberDetail>("PUT", `/internal/members/${memberNumber}/contact`, contact),
  addNote: (memberNumber: string, body: string) =>
    request<Note>("POST", `/internal/members/${memberNumber}/notes`, { body }),
  deleteNote: (id: number) => request<void>("DELETE", `/internal/notes/${id}`),
  claim: (claimNumber: string) => request<ClaimDetail>("GET", `/internal/claims/${claimNumber}`),
  requestReconsideration: (claimNumber: string) =>
    request<Claim>("POST", `/internal/claims/${claimNumber}/reconsideration`),
  submitReimbursement: (body: Record<string, string>) =>
    request<Reimbursement>("POST", "/internal/reimbursements", body),
};
