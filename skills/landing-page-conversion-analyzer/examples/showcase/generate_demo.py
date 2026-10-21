#!/usr/bin/env python3
"""Generate synthetic, deterministic daily aggregates. No customer data."""
import csv
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
columns = ["date","landing_page","utm_source","utm_medium","utm_campaign","country","device","new_vs_returning","sessions","step_view_item","step_add_to_cart","step_begin_checkout","step_purchase","revenue"]
rows = []
start = date(2026, 7, 25)
for day in range(42):
    current = day >= 35
    for page in ["/starter-kit", "/refill"]:
        for source, medium in [("google", "cpc"), ("newsletter", "email"), ("bing", "organic")]:
            n = 2000
            purchase = 40 if current and page == "/starter-kit" and source == "google" else 80
            rows.append([str(start + timedelta(days=day)),page,source,medium,"evergreen","US","desktop","new",n,1400,400,200,purchase,purchase*100])
for name, selected in [("history.csv",rows[:35*6]),("sessions.csv",rows)]:
    with (ROOT/name).open("w", newline="") as f:
        writer = csv.writer(f,lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(selected)
for name, end in [("baseline-meta.json","2026-08-28"),("scan-meta.json","2026-09-04")]:
    (ROOT/name).write_text(json.dumps({
        "timezone":"UTC", "currency":"USD", "source_system":"synthetic_showcase",
        "landing_page_form":"path","sampled":False,"sampling_rate":None,
        "other_row_sessions":0,"reconciliation_delta_pct":0.0,
        "date_range":["2026-07-25",end],"complete_through":end,
        "extracted_at":end+"T23:59:59Z",
        "synthetic":True
    },indent=2)+"\n")
print("Generated deterministic synthetic data: history.csv and sessions.csv")
