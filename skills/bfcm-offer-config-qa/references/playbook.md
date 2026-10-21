# BFCM Offer & Discount Config QA: Playbook

Every change in this playbook needs the user's explicit approval. After a change, open the discount again and read the setting back, re-export, re-run the check, and run the matching test checkout from §7 of the report. Mark an item **verified** only when a later run and its test cart both show it fixed. Admin labels move; if a path below doesn't match, look for the named section on the discount's page.

## 1. Where each setting lives

Shopify admin → **Discounts** → open the discount. The sections, top to bottom:

| Report term | Admin section | Notes |
| --- | --- | --- |
| class | the discount type: *Amount off products* (product), *Amount off order* (order), *Free shipping* (shipping), *Buy X get Y* (product) | The type can't be changed on an existing discount. Delete it and create it again. |
| method | Method: *Discount code* or *Automatic discount* | |
| value | Value: *Percentage* or *Fixed amount* | |
| target | Applies to: *Specific collections* or *Specific products* | Product discounts only. |
| minimum | Minimum purchase requirements | Amount or quantity. |
| eligibility | Eligibility: *All customers*, *Customer segments*, *Specific customers*, *Markets* | |
| usage limit, once per customer | Maximum discount uses | *Limit number of times this discount can be used in total* and *Limit to one use per customer* (tracked by email or phone). |
| combinations | Combinations | Tick the classes this discount may combine with. |
| window | Active dates | Start and end date and time, in the store's time zone. |

The Discounts list shows each discount's combinations as product, order and shipping icons, and can be filtered by **Combines with**. That is the fastest way to review combinations across many codes.

## 2. Who gets the sale: use a sale-eligible collection

Shopify discounts include; they don't exclude. To leave products out, point *Applies to* at a collection that contains only the products that should be discounted.

- Create an automated collection such as **BFCM Eligible** with the condition *Product tag is equal to* `bfcm-eligible`, and tag the eligible products. A manual collection works too. Check the collection's product count against the plan before using it.
- Don't target broad collections like *Kitchen* or *Textiles* when clearance or new items also sit in them. In the sample store, Last Chance items are in Textiles and Kitchen as well.
- Gift cards are only discounted when the discount names the gift card product, so *All products* and collection targets leave them out.
- An *Amount off order* discount applies to the whole subtotal and can't leave products out. If an offer needs exclusions, build it as *Amount off products*.
- Use the same collection for every discount in the sale (VIP early access and the main sale) so their reach can't drift apart.

## 3. Combinations

How Shopify combines discounts (Help Center, "Combining discounts"):

- A combination applies only if **each** discount allows the other's class. Untick the class on **both** discounts: either side stops the stack, and both sides survive a later edit.
- Product discounts apply first, then order discounts on the reduced subtotal, then one shipping discount. Two percentage order discounts are each taken on the same subtotal (10% + 15% = 25% off).
- Each item gets one product discount, unless both are tagged to combine on the same line, which needs Shopify Plus.
- If discounts can't combine, the customer gets the better one, so a code refused at checkout ("Discount couldn't be used with your existing discounts") is expected when the plan says no stacking.
- Up to 5 product or order codes and 1 shipping code per order; up to 25 active automatic discounts, app discounts included.
- Combinations work on the Online Store, the Storefront API and Shopify POS, not on Facebook, Instagram, Google or other external checkouts.
- Product + order and order + order combinations need checkout extensibility (no `checkout.liquid` customizations).
- Custom discounts applied by hand (POS, draft orders) and discounts added by Shopify Scripts always apply, whatever the combination settings say. If the store runs Scripts or a discount app, checkout can differ from this model: test it.

A typical BFCM combination plan: the sitewide sale combines with shipping only; welcome, VIP and creator codes combine with shipping only; free shipping combines with product and order discounts.

## 4. Codes: limits and hygiene

- **Creator codes** (MAYA20): one code per creator, a total usage limit that matches the deal, *Limit to one use per customer*, and an end date. NAME+NUMBER codes get reposted on coupon sites within hours, and coupon browser extensions try them. Watch usage daily during the sale (BFCM Live Pulse) and lower the limit if usage jumps.
- **VIP and early-access codes**: limit eligibility to the customer segment and check the segment's size. A guessable code matters less when it only works for the segment.
- **Welcome codes**: one shared code shown in a pop-up is public by design. Limit it to a never-purchased segment and one use per customer, or use unique codes from your email platform.
- **Old codes**: deactivate them; don't leave them waiting. Reactivating an expired discount makes it active **with no end date**, so set an end date whenever you reactivate one for a customer.
- **Usage limits** count every use since the discount was created. A 300-use plan on a code that already has 118 uses leaves 182.
- **"Already used" with no order**: Shopify holds a code against its limit while a payment is in progress. Support can check the customer's orders or issue a replacement code.

## 5. Free shipping

- Set *Minimum purchase amount* on every free-shipping discount, automatic and code.
- The minimum counts products at their discounted price when the discounts combine. During a 25% sale, a $78 item ($58.50 after the sale) no longer reaches $75. Decide whether that's intended and tell support before shoppers ask.
- Gift cards bought in the order count toward the free-shipping minimum.
- Only one shipping discount applies per order. When a shipping discount can't combine with a product or order discount, the product or order discount wins.
- The report's estimate is orders under the minimum last BFCM × your shipping cost per order. It is an estimate, not a forecast.

## 6. Windows, time zones and DST

- In the admin, start and end times are in the store's time zone (Settings → General → Store defaults). A discount set to start on November 26 starts at 12:00 am store time.
- Apps, CSV imports and API scripts send a timestamp. Send UTC with `Z` or an explicit offset, worked out for the date in question: 11:59 pm ET on Nov 25, 2026 is `2026-11-26T04:59:00Z`. A bare `23:59Z` is 6:59 pm in New York.
- US daylight time ends Sunday Nov 1, 2026. ET is UTC−4 until then and UTC−5 after, so a script that hardcodes `-04:00` for BFCM dates is an hour off.
- End each phase at 11:59 pm and start the next at 12:00 am. The report treats that one minute as continuous.
- If two phases overlap and are allowed to combine, they stack during the overlap. The report prints the overlap and the stack.

## 7. Testing discounts before launch

The report's §7 gives each test as a time, a customer, a cart, a code, the total to expect, and the total that shows the bug. Totals are merchandise after discounts, before shipping and tax.

- **Scheduled discounts can't be applied before they start.** For a sale-phase row, create a staff-only copy of the discount with the same settings: eligibility limited to a customer segment that contains only your test customer (for example, tag the customer `qa-test` and use a segment on that tag), active from now for an hour. Run the cart, then delete the copy. If you can't limit an automatic discount that way, test on a development or staging store, or in the first minutes after the phase starts and before you announce it.
- Shopify suggests testing combinations with a [test order](https://help.shopify.com/en/manual/checkout-settings/test-orders/processing-test-order), or by going through checkout, entering the codes and stopping before payment. Try products the discount excludes as well as included ones, and customers in and out of each segment.
- If the store uses rollouts, Shopify describes using a rollout to schedule a discount or test it against the discounts already running.
- Shopify may round a cent differently from the report. A difference of a cent or two is not a bug; a different discount line is.

## 8. Reference prices (compare-at)

The FTC's Guides Against Deceptive Pricing, [16 CFR 233.1](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-B/part-233), treat a former price as a legitimate basis for a price comparison when it is "the actual, bona fide price at which the article was offered to the public on a regular basis for a reasonably substantial period of time", and give as an example of a fictitious comparison a price "that was not maintained for a reasonable length of time, but was immediately reduced". On a product page, a compare-at price reads as that former price.

Practice (not legal advice; have counsel review your pricing claims, and check state rules, which can be stricter):
- For a markdown, set compare-at to the price the item actually sold at before it.
- Don't raise compare-at in the run-up to a sale. If a price genuinely changed, let the new regular price run for a meaningful period before discounting from it.
- Keep price history (exports or an app) so you can show what was charged and when.
- Clear compare-at prices that sit below the selling price. Product Feed Auditor checks the Google feed's sale price separately.

The report's 30-of-90-days rule is a review trigger, not a legal standard.

## 9. Timeline to BFCM 2026 (sample store dates)

| By | Step |
| --- | --- |
| Today | Run the check. Fix anything live today: free-shipping minimum, codes that must not be active. |
| Nov 10 | Every configuration fix in place (launch − 14 days). |
| Nov 17 | Re-run on a fresh export: READY. Run the test checkouts. Mark verified items. |
| Nov 21 | Discount freeze: no new or edited discounts without a re-run. |
| Nov 23 | Final export and run. |
| Nov 24, 12:05 am ET | One order (or test order) in VIP early access; read the discount lines. |
| Nov 26, 12:05 am ET | Same for the main sale. |
| Nov 24 – Dec 1 | Daily: code usage against limits (BFCM Live Pulse). |
| Dec 2 | Confirm every sale discount expired; set end dates on anything left open. |

## 10. Status update template

```
**BFCM discount QA: <store>**, <date>. Verdict: <READY | FIX BEFORE LAUNCH | NOT READY>

Detected
- <Main sale applies to All products: N clearance/new SKUs get X% (planned: excluded)>
- <WELCOME10 + sale stack to Y% (max Z%); N SKUs below unit cost>
- <Free shipping minimum $0 (planned $75); ~$A over BFCM (estimate)>

Recommended (nothing changed yet), fix by <date>
1. <fix>
2. <fix>

Applied: <item, date, who>    Verified: <item, run date, test cart #>
```

## Sources

- Shopify Help Center: [Combining discounts](https://help.shopify.com/en/manual/discounts/discount-combinations), [Amount off discounts](https://help.shopify.com/en/manual/discounts/discount-types/percentage-fixed-amount), [Free shipping discounts](https://help.shopify.com/en/manual/discounts/discount-types/free-shipping), [Discounts FAQ](https://help.shopify.com/en/manual/discounts/discounts-faq), [Add a discount to a gift card](https://help.shopify.com/en/manual/discounts/discount-gift-card), [Managing discounts](https://help.shopify.com/en/manual/discounts/managing-discounts).
- Shopify GraphQL Admin API 2026-10: [discountNodes](https://shopify.dev/docs/api/admin-graphql/latest/queries/discountNodes), [DiscountCombinesWith](https://shopify.dev/docs/api/admin-graphql/latest/objects/DiscountCombinesWith), [DiscountCodeBasic](https://shopify.dev/docs/api/admin-graphql/latest/objects/DiscountCodeBasic), [DiscountAutomaticBasic](https://shopify.dev/docs/api/admin-graphql/latest/objects/DiscountAutomaticBasic).
- FTC Guides Against Deceptive Pricing: [16 CFR Part 233](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-B/part-233).
