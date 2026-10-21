import { Result } from "neverthrow";
import { z } from "zod";

const resolveTimezone = Result.fromThrowable(
  (value: string) => new Intl.DateTimeFormat("en-US", { timeZone: value }).resolvedOptions().timeZone,
  () => "invalid_timezone",
);
/**
 * One timezone rule for the input schema and the sync engine: any zone name the runtime's Intl
 * accepts (including aliases such as Etc/UTC or US/Eastern), but never a raw UTC offset.
 * Returns the runtime's resolved name, or null.
 */
export function storeTimezone(value: string): string | null {
  if (value.length < 1 || value.length > 100 || /^[+-]/.test(value.trim())) return null;
  const resolved = resolveTimezone(value);
  return resolved.isOk() && !/^[+-]/.test(resolved.value) ? resolved.value : null;
}

const CONTROL = /[\u0000-\u001f\u007f-\u009f\p{Zl}\p{Zp}]/u;
/** Names QuickBooks matches exactly. They are never rewritten, so unsafe values are rejected. */
export const identityText = z.string().min(1).max(100).refine(
  (value) => value === value.trim() && !CONTROL.test(value) && !/^[=+\-@]/.test(value),
  "Names must be trimmed, contain no control characters, and not start with = + - @",
);
const sku = z.string().min(1).max(100).refine((value) => !CONTROL.test(value), "Invalid SKU");
const currency = z.enum(["USD", "CAD", "GBP", "EUR", "AUD", "NZD"]);

export const configSchema = z.strictObject({
  storeHash: z.string().regex(/^[A-Za-z0-9_-]{1,40}$/),
  currency,
  region: z.enum(["US", "non_US"]),
  eligibleStatusIds: z.array(z.number().int().min(1).max(100)).min(1).max(20)
    .refine((ids) => !ids.some((id) => [4, 5, 6, 14].includes(id)), "Refunded/cancelled statuses cannot be eligible"),
  itemMap: z.record(sku, identityText).refine((map) => Object.keys(map).length <= 5000, "At most 5000 mapped SKUs"),
  shippingItemName: identityText.optional(),
  zeroTaxCodeName: identityText.optional(),
  allowNewCustomers: z.boolean().default(false),
  customerNameSource: z.enum(["billing_name", "company_then_name", "email"]).default("billing_name"),
  paidOrderTreatment: z.enum(["sales_receipt_worksheet", "invoice_then_payment"]).default("sales_receipt_worksheet"),
  paymentMethodName: identityText.optional(),
  depositAccountName: identityText.optional(),
  purchaseOrders: z.boolean().default(false),
  suppliers: z.record(sku, z.strictObject({
    vendorName: identityText,
    unitCostCents: z.number().int().min(1).max(10_000_000_000),
    currency,
    verified: z.boolean(),
  })).default({}),
  invoicePrefix: z.string().regex(/^[A-Za-z][A-Za-z0-9-]{0,5}$/).default("BC-"),
  termsDays: z.number().int().min(0).max(365).default(0),
  termsName: identityText.optional(),
  dateFormat: z.enum(["MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD"]).default("MM/DD/YYYY"),
  qboLimits: z.strictObject({
    maxCustomerPages: z.number().int().min(1).max(1000).default(100),
    maxVendorPages: z.number().int().min(1).max(1000).default(20),
    maxInvoicePages: z.number().int().min(1).max(1000).default(50),
  }).default({ maxCustomerPages: 100, maxVendorPages: 20, maxInvoicePages: 50 }),
}).superRefine((config, ctx) => {
  if (config.region === "non_US" && !config.zeroTaxCodeName)
    ctx.addIssue({ code: "custom", message: "Non-US invoice imports need the zero-rated tax code name", path: ["zeroTaxCodeName"] });
  if (config.region === "US" && config.zeroTaxCodeName)
    ctx.addIssue({ code: "custom", message: "US invoice imports have no tax code column", path: ["zeroTaxCodeName"] });
});
export type Config = z.output<typeof configSchema>;
