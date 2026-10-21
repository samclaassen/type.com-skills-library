# Holiday 2026 macros: Fernhill Goods (drafts for review)

Generated 2026-10-07 from policies.json. 17 macros. Facts come only from the policies; review the tone, not the facts. If a fact is wrong, fix policies.json and re-run.

## Map the placeholders to your helpdesk

| Placeholder | Gorgias | Zendesk | Help Scout (saved replies) | Shopify Inbox (quick replies) |
| --- | --- | --- | --- | --- |
| `{{customer_first_name}}` | `{{ticket.customer.firstname}}` (confirmed) | `{{ticket.requester.first_name}}` (confirmed) | `{%customer.firstName,fallback=there%}` (confirmed) | No variables documented: type it in |
| `{{agent_first_name}}` | `{{current_user.firstname}}` (confirmed) | `{{current_user.first_name}}` (confirmed) | `{%user.firstName%}` (confirmed) | Type it in |
| `{{order_number}}` | Not in Gorgias' list (`orders[0].id` is Shopify's internal ID, not the order number). Commonly `{{ticket.customer.integrations.shopify.orders[0].name}}`: verify in your helpdesk | No built-in placeholder. A custom ticket field `{{ticket.ticket_field_<field ID>}}` filled by your Shopify app: verify in your helpdesk | No variable: type it in | Type it in |
| `{{tracking_url}}` | Not in Gorgias' list. Commonly `{{ticket.customer.integrations.shopify.orders[0].fulfillments[0].tracking_url}}`: verify in your helpdesk | Custom ticket field, as above: verify in your helpdesk | No variable: type it in | Type it in |

"Confirmed" = shown in the helpdesk's own documentation on Oct 7, 2026:
Gorgias https://helpcenter.gorgias.com/en-US/macro-variables-101-81845 ·
Zendesk https://support.zendesk.com/hc/en-us/articles/4408886858138-Zendesk-Support-placeholders-reference ·
Help Scout https://docs.helpscout.com/article/468-work-with-variables ·
Shopify Inbox https://help.shopify.com/en/manual/inbox/configure-inbox/quick-replies
Gorgias `orders[0]` is the customer's most recent order, which may not be the one the ticket is about, and Gorgias
replaces a variable it can't fill with a blank. Any other helpdesk: verify in your helpdesk.

## Holiday 2026 - WISMO: not shipped yet

Ticket type: Where is my order (WISMO) · Tags: holiday-2026, wismo, processing · Use: all season · Facts from: shipping.same_day_cutoff, bfcm_backlog, shipping.closed_dates

```text
Hi {{customer_first_name}},

Thanks for checking on order {{order_number}}. Orders placed by 1 pm ET on a business day usually ship the same day, but not during our Black Friday rush: orders placed from Thursday, November 26 to Monday, November 30 ship within 3 business days, and same-day shipping resumes once we're caught up, which we expect by Thursday, December 3. Once yours has shipped, reply here any time and I'll send you the tracking link.
Our warehouse is closed on Thursday, November 26 and Friday, December 25.

Thanks for your patience!
{{agent_first_name}}
```

## Holiday 2026 - WISMO: BFCM order not shipped yet

Ticket type: Where is my order (WISMO) · Tags: holiday-2026, wismo, bfcm-backlog · Use: Nov 26–Dec 3 · Facts from: bfcm_backlog

```text
Hi {{customer_first_name}},

Thank you for your order {{order_number}}, and for your patience while we work through our Black Friday orders. Orders placed from Thursday, November 26 to Monday, November 30 ship within 3 business days, and we expect to be fully caught up by Thursday, December 3.

Once your order has shipped, reply here any time and I'll send you the tracking link.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - WISMO: shipped, in transit

Ticket type: Where is my order (WISMO) · Tags: holiday-2026, wismo, in-transit · Use: all season · Facts from: none

```text
Hi {{customer_first_name}},

Good news: order {{order_number}} is on its way. You can follow it here: {{tracking_url}}

Carriers are busier than usual this time of year, so tracking can take a little longer to update. If it looks stuck, reply to this email and we'll check with the carrier for you.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Shipping: holiday order-by dates

Ticket type: Shipping deadline question · Tags: holiday-2026, shipping-deadline, order-by-dates · Use: until Dec 14 · Facts from: shipping.deliver_by, shipping.services, shipping.delivery_coverage_pct, shipping.same_day_cutoff, bfcm_backlog

```text
Hi {{customer_first_name}},

Here are the last days to order for delivery by Thursday, December 24:
- Standard: order by Monday, December 14
- Express: order by Monday, December 21
These dates hold for most addresses (95% of our customers).

Orders placed by 1 pm ET on a business day usually ship the same day, but not during our Black Friday rush: orders placed from Thursday, November 26 to Monday, November 30 ship within 3 business days, and same-day shipping resumes once we're caught up, which we expect by Thursday, December 3.
Choose Standard or Express at checkout. If you've already ordered, reply with your order number and I'll check which one you picked.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Shipping: only the fastest method is still on time

Ticket type: Shipping deadline question · Tags: holiday-2026, shipping-deadline, last-option · Use: Dec 15–Dec 21 · Facts from: shipping.services, shipping.deliver_by, shipping.delivery_coverage_pct, gift_options.e_gift_cards

```text
Hi {{customer_first_name}},

The last order date has passed for Standard, but there's still time: choose Express at checkout and order by Monday, December 21 for delivery by Thursday, December 24.
That date holds for most addresses (95% of our customers).
Our digital gift cards are emailed right away, or on a date you choose.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Shipping: after the last order date

Ticket type: Shipping deadline question · Tags: holiday-2026, shipping-deadline, after-cutoff · Use: from Dec 22 · Facts from: shipping.deliver_by, shipping.services, gift_options.e_gift_cards

```text
Hi {{customer_first_name}},

I'm sorry, the last day to order for delivery by Thursday, December 24 was Monday, December 21. We're still shipping every order, but we can't promise it will get there in time.
Our digital gift cards are emailed right away, or on a date you choose.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Discount: code not working

Ticket type: Discount code not working · Tags: holiday-2026, discount-code, code-rules · Use: all season · Facts from: discounts.one_code_per_order, discounts.codes_combine_with_sale, discounts.sale.starts, discounts.sale.ends, discounts.sale.automatic, discounts.free_shipping_threshold

```text
Hi {{customer_first_name}},

Sorry for the trouble at checkout! Only one discount code can be used per order. Discount codes can't be combined with the BFCM sale (25% off sitewide).
The sale runs from Thursday, November 26 to Tuesday, December 1.
The sale price comes off automatically at checkout, so you don't need a code for it.
Free shipping on orders of $75 or more still applies on top of a code or the sale.

If your total still doesn't look right, reply with a screenshot of your cart and I'll take a look.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Discount: item not in the sale

Ticket type: Discount code not working · Tags: holiday-2026, discount-code, sale-exclusions · Use: Nov 26–Dec 1 · Facts from: discounts.sale.excludes, discounts.sale, discounts.one_code_per_order

```text
Hi {{customer_first_name}},

Thanks for reaching out! Items in our Gift Cards, New Arrivals and Last Chance collections aren't part of the BFCM sale (25% off sitewide), so the discount doesn't apply to them.
Only one discount code can be used per order.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Order: change or cancel

Ticket type: Cancel or change an order · Tags: holiday-2026, cancel-modify, edit-window · Use: all season · Facts from: order_changes.window_minutes, order_changes.how, returns.start_url, returns.final_sale

```text
Hi {{customer_first_name}},

We can change or cancel an order within 1 hour of it being placed.
Reply to your order confirmation email or message us with your order number.
Once that time has passed, we can't change or cancel it.
Once it arrives, you can return eligible items: start at fernhillgoods.com/returns.
Items from Last Chance and Gift Cards are final sale.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Order: change the shipping address

Ticket type: Address change · Tags: holiday-2026, address-change, address · Use: all season · Facts from: address_change.until, address_change.after_shipping

```text
Hi {{customer_first_name}},

We can update the shipping address on order {{order_number}} until your order ships. Reply with the full new address, including any apartment or unit number, and I'll confirm once it's changed.
Once a package has shipped, we can't redirect it.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Returns: holiday window

Ticket type: Return or exchange · Tags: holiday-2026, return-exchange, holiday-window · Use: until Jan 31, 2027 · Facts from: returns.holiday_window, returns.standard_days, returns.condition, returns.start_url, returns.return_label_fee, returns.refund_to, returns.exchanges, returns.final_sale

```text
Hi {{customer_first_name}},

Good news: orders placed from Sunday, November 1 to Thursday, December 24 can be returned until Sunday, January 31, 2027, so there's no rush. Other orders can be returned within 30 days of delivery.
Items need to be unused and in the original packaging.
Start your return at fernhillgoods.com/returns.
Return shipping is free.
Refunds go back to your original payment method.
Want something else instead? We're happy to exchange it.
Items from Last Chance and Gift Cards are final sale.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Returns: returning a gift

Ticket type: Return or exchange · Tags: holiday-2026, return-exchange, gift-return · Use: all season · Facts from: returns.gift_returns, returns.holiday_window, returns.start_url

```text
Hi {{customer_first_name}},

Received a gift from us? You can return it for store credit with the order number from the packing slip, and we won't tell the sender.
Gifts ordered from Sunday, November 1 to Thursday, December 24 can be returned until Sunday, January 31, 2027.
Start the return at fernhillgoods.com/returns.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Damaged or defective item

Ticket type: Damaged or defective · Tags: holiday-2026, damaged-defective, damaged · Use: all season · Facts from: damaged_defective.photo_required, damaged_defective.resolution, damaged_defective.report_within_days, escalation.refund_over, escalation.owner, damaged_defective.return_required

```text
Hi {{customer_first_name}},

I'm so sorry your order arrived damaged.
Could you reply with a photo of the item and the box it came in?
We'll send you a free replacement, or a full refund if it's sold out. Please let us know about any damage within 14 days of delivery.
If that's worth more than $150, the support lead approves it first, and I'll let you know as soon as they have.
You don't need to send the damaged item back.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Gift options

Ticket type: Gift options · Tags: holiday-2026, gift-options, gifting · Use: all season · Facts from: gift_options.gift_message, gift_options.gift_receipt, gift_options.gift_wrap, gift_options.ship_to_another_address, gift_options.e_gift_cards

```text
Hi {{customer_first_name}},

Happy to help with your gift!
You can add a gift message to your order.
We can include a gift receipt.
We don't offer gift wrapping.
You can ship to a different address from your billing address: just enter the recipient's address at checkout.
Our digital gift cards are emailed right away, or on a date you choose.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Out of stock: when is it back?

Ticket type: Out of stock or backorder · Tags: holiday-2026, out-of-stock, restock · Use: all season · Facts from: out_of_stock.backorders, out_of_stock.restock_alerts

```text
Hi {{customer_first_name}},

Thanks for your interest! When an item sells out, we can't take orders for it until it's back in stock.
You can sign up for a restock alert, and we'll let you know when it's back.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Holiday support hours

Ticket type: General · Tags: holiday-2026, general, support-hours · Use: all season · Facts from: support_hours.regular, support_hours.first_reply_hours, support_hours.holiday_note, support_hours.closed_dates

```text
Hi {{customer_first_name}},

Thanks for your message! Our support team is available Monday to Friday, 9 am to 5 pm ET, and we reply within 24 hours.
From November 28 to December 20 we also answer on weekends, 10 am to 2 pm ET.
We're closed on Thursday, November 26, Friday, December 25 and Friday, January 1, 2027.

Thanks,
{{agent_first_name}}
```

## Holiday 2026 - Shipping: international orders

Ticket type: General · Tags: holiday-2026, general, international · Use: all season · Facts from: international.ships_to

```text
Hi {{customer_first_name}},

Thanks for asking! We ship to the United States only, including APO/FPO addresses.

Thanks,
{{agent_first_name}}
```

## Not generated (policy missing)

- Holiday 2026 - Price adjustment: request (we offer them) and Holiday 2026 - Price adjustment: request (we don't offer them): need price_adjustment in policies.json. Decide the policy, add it and re-run.
