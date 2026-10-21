# References

Reviewed 2026-10-16. No live BigCommerce or QuickBooks account was used to build or test this package; all tests use synthetic fixtures.

## QuickBooks Online invoice import (CSV)

- US: "Import multiple invoices at once" — https://quickbooks.intuit.com/learn-support/en-us/help-article/import-export-data-files/import-multiple-invoices/L7E9Xrd8l_US_en_US
  Required per line: invoice number, customer, invoice date, due date, item amount. Up to 1,000 rows per file, at most 100 invoices per import, no negative charges (discounts/credit memos). Not available when sales tax is set up in the company. Columns are mapped in the import wizard (unused fields → Not applicable; missing item → generic "Sales"); date format is chosen during import. Sample CSV: Settings ⚙ → Import data → Invoices → Download sample csv.
- Non-US (UK edition): https://quickbooks.intuit.com/learn-support/en-uk/help-article/import-export-data-files/import-multiple-invoices/L7E9Xrd8l_GB_en_GB
  Adds a required item tax code per line, an Exclusive/Inclusive VAT choice, and tax-code matching during import.

Column headers used here (`InvoiceNo, Customer, InvoiceDate, DueDate, Terms, Memo, Item(Product/Service), ItemDescription, ItemQuantity, ItemRate, ItemAmount` [+ `ItemTaxCode, ItemTaxAmount` non-US]) follow the field names of Intuit's sample file as commonly distributed. The help articles above do not print the sample's exact header row, and the wizard maps columns by hand, so header names are not load-bearing; verify against the sample downloaded from the target company before the first import. The skill splits files at 100 invoices / 998 data rows.

Customer import (`qbo-customers-import.csv`) and the receipt/payment/PO worksheets are likewise mapped or entered manually; QuickBooks Online has no CSV import for sales receipts, payments or purchase orders.

## QuickBooks reads (native Type tools)

`quickbooks_get_company_info` (CompanyInfo.Country), `quickbooks_list_entities` (Customer, Vendor, Invoice; `startPosition` 1–100000, `maxResults` 1–100), `quickbooks_get_entity`, `quickbooks_run_report` (report Header.Currency for home currency). Entity fields used: Customer `Id, DisplayName, Active, PrimaryEmailAddr.Address, CurrencyRef`; Vendor `DisplayName, Active`; Invoice `Id, DocNumber, TotalAmt, Balance, CustomerRef.name`.
Entity reference: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/invoice
The native list tool issues `SELECT * FROM <entity> STARTPOSITION n MAXRESULTS m` without ORDERBY (Type server source, `toolDefinitions.ts`). The stability of the server-side order between pages was not verified against a live company; the skill re-reads 5 overlapping rows per page boundary and blocks (`qbo_<entity>_paging_inconsistent`) rather than skip rows when the order or content shifts. `quickbooks_get_entity` takes `{ entity, id }` and returns one entity by QuickBooks Id.

## BigCommerce (GET only)

- Orders V2: https://docs.bigcommerce.com/developer/api-reference/rest/admin/management/orders — `default_currency_code` is the transactional currency.
- List orders: https://docs.bigcommerce.com/developer/api-reference/rest/admin/management/orders/get-orders — `min_date_modified`, `max_date_modified`, `min_id`, `sort=id:asc`, `limit`.
- Order products: https://docs.bigcommerce.com/developer/api-reference/rest/admin/management/orders/order-products/get-order-products
- API accounts and scopes: https://docs.bigcommerce.com/developer/docs/start/authentication/api-accounts
