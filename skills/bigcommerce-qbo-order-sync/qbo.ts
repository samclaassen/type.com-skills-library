import type { Config } from "./config";

/** The only QuickBooks tools this skill may call. All are read-only native Type tools. */
export const QBO_READ_TOOLS = [
  "quickbooks_get_company_info",
  "quickbooks_list_entities",
  "quickbooks_get_entity",
  "quickbooks_run_report",
] as const;
export type QboTool = (typeof QBO_READ_TOOLS)[number];
export type QboArgs = Record<string, string | number>;
export type QboOutcome = { ok: true; data: unknown } | { ok: false; error: string; retryable: boolean };
export type QboCall = (tool: QboTool, args: QboArgs) => Promise<QboOutcome>;

export type QboInvoice = { id: string; docNumber: string; totalCents: number | null; customerName: string | null; balanceCents: number | null };
export type QboSnapshot = {
  country: string | null; homeCurrency: string | null; warnings: string[];
  customers: Record<string, unknown>[]; customersComplete: boolean;
  vendors: Record<string, unknown>[] | null; vendorsComplete: boolean;
  invoices: Map<string, QboInvoice[]>; invoicesComplete: boolean;
};

const PAGE = 100;
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const cents = (v: unknown): number | null => {
  if (typeof v !== "number" && typeof v !== "string") return null;
  const m = /^(-?)(\d{1,12})(?:\.(\d{1,2}))?$/.exec(String(v));
  if (!m) return null;
  const value = Number(m[2]) * 100 + Number((m[3] ?? "").padEnd(2, "0"));
  return m[1] ? -value : value;
};

async function call(qbo: QboCall, sleep: (ms: number) => Promise<void>, tool: QboTool, args: QboArgs): Promise<QboOutcome> {
  let last: QboOutcome = { ok: false, error: "qbo_unavailable", retryable: false };
  for (let attempt = 0; attempt < 3; attempt++) {
    last = await qbo(tool, args);
    if (last.ok || !last.retryable || attempt === 2) return last;
    // Back off between attempts only; never after the final one.
    await sleep(2000 * 2 ** attempt);
  }
  return last;
}

/** Rows re-read at the start of each next page to prove the list did not shift between pages. */
export const PAGE_OVERLAP = 5;
const rowKey = (row: Record<string, unknown>) => (row.Id === undefined ? "row:" + JSON.stringify(row) : "id:" + String(row.Id));
type Listed = { ok: true; records: Record<string, unknown>[]; complete: boolean } | { ok: false; reason: string };

/**
 * Bounded, verified paging. Each next page starts PAGE_OVERLAP rows before the end of the previous one, and those
 * rows must come back identical and in the same positions. An insertion or deletion before the page boundary
 * (offset paging over a changing list), a tool that ignores startPosition, or a repeated Id therefore shows up as a
 * mismatch instead of a silently skipped row. A mismatching read is retried once from the start, then reported as
 * qbo_<entity>_paging_inconsistent. A list is complete only when a page brings no new rows; reaching maxPages is
 * reported as incomplete, never as absence. Paging advances by the rows actually returned (tools may cap pages).
 */
export async function listAll(qbo: QboCall, sleep: (ms: number) => Promise<void>, entity: "Customer" | "Vendor" | "Invoice", maxPages: number): Promise<Listed> {
  const first = await listOnce(qbo, sleep, entity, maxPages);
  if (first.ok || !first.reason.endsWith("_paging_inconsistent")) return first;
  return listOnce(qbo, sleep, entity, maxPages);
}

async function listOnce(qbo: QboCall, sleep: (ms: number) => Promise<void>, entity: "Customer" | "Vendor" | "Invoice", maxPages: number): Promise<Listed> {
  const name = entity.toLowerCase();
  const inconsistent = { ok: false as const, reason: "qbo_" + name + "_paging_inconsistent" };
  const records: Record<string, unknown>[] = [];
  const order: string[] = [];
  const seen = new Set<string>();
  let startPosition = 1, overlap = 0;
  // maxPages bounds the pages that bring new records; one extra request may only confirm the end.
  for (let page = 0; page <= maxPages && startPosition <= 100000; page++) {
    const result = await call(qbo, sleep, "quickbooks_list_entities", { entity, startPosition, maxResults: PAGE });
    if (!result.ok) return { ok: false, reason: "qbo_" + name + "_read_failed" };
    const response = record(result.data) ? result.data.QueryResponse : undefined;
    if (!record(response)) return { ok: false, reason: "qbo_" + name + "_shape_invalid" };
    const rows = response[entity] ?? [];
    if (!Array.isArray(rows) || rows.length > PAGE || rows.some((r) => !record(r))) return { ok: false, reason: "qbo_" + name + "_shape_invalid" };
    const typed = rows as Record<string, unknown>[];
    // The overlap rows must be exactly the last rows already read, in the same order.
    if (typed.length < overlap) return inconsistent;
    for (let i = 0; i < overlap; i++) if (rowKey(typed[i] as Record<string, unknown>) !== order[order.length - overlap + i]) return inconsistent;
    const fresh = typed.slice(overlap);
    if (fresh.length === 0) return { ok: true, records, complete: true };
    // More rows follow a page too short to give the full overlap: that boundary is unverified, so the list is incomplete.
    if (page > 0 && overlap < PAGE_OVERLAP) break;
    if (page === maxPages) break;
    for (const row of fresh) {
      const key = rowKey(row);
      if (seen.has(key)) return inconsistent;
      seen.add(key); order.push(key); records.push(row);
    }
    const next = Math.min(PAGE_OVERLAP, typed.length - 1);
    startPosition += typed.length - next;
    overlap = next;
  }
  return { ok: true, records, complete: false };
}

/**
 * Targeted read of one invoice by its QuickBooks Id (quickbooks_get_entity). "present" only when QuickBooks
 * returns that invoice; any error is "unknown" (deleted, voided or unavailable cannot be told apart).
 */
export async function invoiceById(qbo: QboCall, sleep: (ms: number) => Promise<void>, id: string): Promise<"present" | "unknown"> {
  const result = await call(qbo, sleep, "quickbooks_get_entity", { entity: "Invoice", id });
  if (!result.ok || !record(result.data)) return "unknown";
  const invoice = record(result.data.Invoice) ? result.data.Invoice : result.data;
  return String(invoice.Id ?? "") === id ? "present" : "unknown";
}

const COUNTRY: Record<string, string> = { US: "US", USA: "US", "United States": "US" };

/** Read-only QuickBooks evidence: company, customers, vendors and invoices (for posted detection). */
export async function readQuickBooks(qbo: QboCall, sleep: (ms: number) => Promise<void>, config: Config, today: string) {
  const warnings: string[] = [];
  const company = await call(qbo, sleep, "quickbooks_get_company_info", {});
  if (!company.ok) return { ok: false as const, reason: "qbo_company_read_failed" };
  const info = record(company.data) && record(company.data.CompanyInfo) ? company.data.CompanyInfo : null;
  const rawCountry = info && typeof info.Country === "string" ? info.Country : null;
  const country = rawCountry === null ? null : COUNTRY[rawCountry] ?? "non_US";
  if (country === null) warnings.push("QuickBooks company country unavailable; region not verified.");
  else if ((country === "US") !== (config.region === "US")) return { ok: false as const, reason: "qbo_company_region_mismatch" };
  // Report headers carry the company home currency; company info does not.
  const report = await call(qbo, sleep, "quickbooks_run_report", { report: "ProfitAndLoss", startDate: today, endDate: today });
  const header = report.ok && record(report.data) && record(report.data.Header) ? report.data.Header : null;
  const homeCurrency = header && typeof header.Currency === "string" && /^[A-Z]{3}$/.test(header.Currency) ? header.Currency : null;
  if (homeCurrency === null) warnings.push("QuickBooks home currency unavailable; currency not verified.");
  else if (homeCurrency !== config.currency) return { ok: false as const, reason: "qbo_home_currency_mismatch" };

  const customers = await listAll(qbo, sleep, "Customer", config.qboLimits.maxCustomerPages);
  if (!customers.ok) return customers;
  if (!customers.complete) warnings.push("QuickBooks customer list exceeded the page limit; matching is unknown.");
  let vendors: Record<string, unknown>[] | null = null, vendorsComplete = false;
  if (config.purchaseOrders && Object.keys(config.suppliers).length) {
    const listed = await listAll(qbo, sleep, "Vendor", config.qboLimits.maxVendorPages);
    if (!listed.ok) return listed;
    vendors = listed.records; vendorsComplete = listed.complete;
    if (!listed.complete) warnings.push("QuickBooks vendor list exceeded the page limit; vendors left unverified.");
  }
  const invoices = await listAll(qbo, sleep, "Invoice", config.qboLimits.maxInvoicePages);
  if (!invoices.ok) return invoices;
  if (!invoices.complete) warnings.push("QuickBooks invoice list exceeded the page limit; unseen invoices are treated as unknown, not as unposted.");
  const index = new Map<string, QboInvoice[]>();
  for (const invoice of invoices.records) {
    const docNumber = invoice.DocNumber;
    if (typeof docNumber !== "string" || !docNumber.startsWith(config.invoicePrefix)) continue;
    const customer = record(invoice.CustomerRef) && typeof invoice.CustomerRef.name === "string" ? invoice.CustomerRef.name : null;
    const entry = { id: String(invoice.Id ?? ""), docNumber, totalCents: cents(invoice.TotalAmt), customerName: customer, balanceCents: cents(invoice.Balance) };
    index.set(docNumber, [...(index.get(docNumber) ?? []), entry]);
  }
  return {
    ok: true as const,
    value: {
      country, homeCurrency, warnings, customers: customers.records, customersComplete: customers.complete,
      vendors, vendorsComplete, invoices: index, invoicesComplete: invoices.complete,
    } satisfies QboSnapshot,
  };
}

export type PostedCheck = { result: "same"; id: string } | { result: "different" | "duplicate" | "not_found" | "unknown" };
/** A match needs DocNumber, total and customer. Absence counts only after complete paging. */
export function postedCheck(snapshot: QboSnapshot, docNumber: string, totalCents: number, customerName: string): PostedCheck {
  const found = snapshot.invoices.get(docNumber) ?? [];
  if (found.length > 1) return { result: "duplicate" };
  const invoice = found[0];
  if (invoice)
    return invoice.totalCents === totalCents && invoice.customerName === customerName && invoice.id
      ? { result: "same", id: invoice.id } : { result: "different" };
  return { result: snapshot.invoicesComplete ? "not_found" : "unknown" };
}
