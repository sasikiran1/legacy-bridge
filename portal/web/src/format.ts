const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function money(amount: number): string {
  return usd.format(amount);
}

/** ISO date (YYYY-MM-DD) to the portal's MM/DD/YYYY display format. */
export function usDate(iso: string): string {
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${month}/${day}/${year}`;
}

export function usDateTime(iso: string): string {
  const date = new Date(iso);
  return `${usDate(date.toISOString())} ${date.toISOString().slice(11, 16)} UTC`;
}
