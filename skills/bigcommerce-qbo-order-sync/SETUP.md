# Setup

Nothing here creates connections, schedules or writes. Connect integrations through Type's normal UI with the user.

## 1. BigCommerce (required, read-only)

Create a store-level API account in BigCommerce (Settings → Store-level API accounts) with:

- **Orders: read-only** (orders and order products)
- **Products: read-only** (useful while building `itemMap`; the skill itself only calls order endpoints)
- Everything else: none

Add it in Type as a **Custom API** connection: base URL `https://api.bigcommerce.com`, header `X-Auth-Token: <access token>` (static token; entered in the connection form, never in chat). The skill sends only `GET /stores/<storeHash>/v2/orders`, `GET .../v2/orders/<id>` and `GET .../v2/orders/<id>/products`. It never writes order notes, statuses or markers.

## 2. QuickBooks Online (optional, read-only)

Use Type's existing **native QuickBooks** connection. No custom OAuth app is needed. The skill calls only `quickbooks_get_company_info`, `quickbooks_list_entities` (Customer, Vendor, Invoice), `quickbooks_get_entity` (Invoice) and `quickbooks_run_report` (ProfitAndLoss header, for home currency). `quickbooks_get_entity` (Invoice by Id) is used for targeted checks of invoices the journal already records as posted.

**Long-term use needs QuickBooks connected (or periodic manual handling).** Without it invoices are never confirmed: exported invoices stay in the follow-up index, and once it holds 1,000 orders every new order (including sales receipts) goes to `confirmation_backlog_full` review.

With QuickBooks connected: buyers are matched to existing customers by email (ambiguous → review), vendors are checked, company region and home currency are verified, invoices already in QuickBooks are not exported, and posted invoices are confirmed. Without it: files are still produced, customers are marked unverified, and nothing is confirmed (orders stay `exported`).

**Without QuickBooks, `qbo-customers-import.csv` can contain buyers who already exist in QuickBooks** (every buyer is written as "unverified" when `allowNewCustomers` is on). Before importing it, remove rows for existing customers, or skip the customer import and let the invoice import match names. Invoice numbers are also not checked against QuickBooks in that mode.

## 3. Slack (optional)

A native Slack connection plus one approved channel ID (`slackChannel`). One summary per **acknowledged** batch via `slack_post_message` (counts and batch ID; the files are uploaded to Type by the agent, never attached to Slack); no customer data is sent. Nothing is sent when a batch is only prepared.

## 4. Configuration (`config`)

| Field | Meaning |
| --- | --- |
| `storeHash` | BigCommerce store hash |
| `currency` | Home currency; orders in other currencies go to review |
| `region` | `US` or `non_US` (non-US adds tax-code columns and needs `zeroTaxCodeName`) |
| `eligibleStatusIds` | BigCommerce status IDs to export (e.g. 2 Shipped, 10 Completed, 11 Awaiting Fulfillment); refund/cancel statuses are rejected |
| `itemMap` | SKU → exact QuickBooks Product/Service name |
| `shippingItemName` | QuickBooks item for shipping lines |
| `allowNewCustomers`, `customerNameSource` | Whether unknown buyers produce `qbo-customers-import.csv`, and how their name is derived |
| `paidOrderTreatment` | `sales_receipt_worksheet` (default) or `invoice_then_payment` |
| `paymentMethodName`, `depositAccountName` | Printed on receipt/payment worksheets |
| `purchaseOrders`, `suppliers` | SKU → vendor name, verified unit cost in cents, currency |
| `invoicePrefix`, `termsDays`, `termsName`, `dateFormat` | Invoice number prefix (`BC-` → `BC-1001`), due date terms, CSV date format |
| `qboLimits` | Max QuickBooks pages (up to 100 records each) for customers, vendors, invoices. If the invoice list is longer than `maxInvoicePages`, unexported invoices go to `posted_status_unknown` review; raise the limit (max 1,000) |

Names must match QuickBooks exactly, be trimmed, and not start with `= + - @`. Create missing products/services, vendors and (if not importing them) customers in QuickBooks before importing.

## 5. QuickBooks company prerequisites

- **US:** Intuit does not allow invoice import when sales tax is set up in the company. If it is, enter invoices manually from the CSV/worksheet. Taxable orders always go to review.
- **Non-US:** each invoice line needs a tax code; configure the zero-rated code name in `zeroTaxCodeName`. Taxable orders go to review.
- Multicurrency: only home-currency orders are exported.
