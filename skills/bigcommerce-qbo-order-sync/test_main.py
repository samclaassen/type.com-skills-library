"""Tests for the credential-free Python planner (main.py). Run: python3 -m unittest test_main -q"""
import copy
import json
import pathlib
import subprocess
import sys
import unittest

import main

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / "fixture.json").read_text(encoding="utf-8"))


def config(**overrides):
    c = copy.deepcopy(FIXTURE["config"])
    c.update(overrides)
    return c


def orders():
    result = copy.deepcopy(FIXTURE["bigcommerce"]["orders"])
    for o in result:
        o["qbo_txn_date"] = o["date_created"][:10]
    return result


def plan(c=None, os=None, customers="fixture", vendors="fixture", complete=True):
    qb = FIXTURE["quickbooks"]
    data = {"config": c or config(), "orders": os if os is not None else orders(),
            "customers": copy.deepcopy(qb["customers"]) if customers == "fixture" else customers,
            "customersComplete": complete,
            "vendors": copy.deepcopy(qb["vendors"]) if vendors == "fixture" else vendors,
            "vendorsComplete": True}
    return main.plan(data)


def by_id(result):
    return {o["orderId"]: o for o in result["orders"]}


class FixturePlan(unittest.TestCase):
    def test_eligible_orders_and_rejections(self):
        result = by_id(plan())
        self.assertEqual(result["1001"]["kind"], "sales_receipt")
        self.assertEqual(result["1001"]["customer"], {"name": "Example Buyer", "match": "matched", "qboId": "10"})
        self.assertEqual([(l["type"], l["amountCents"]) for l in result["1001"]["lines"]],
                         [("product", 2000), ("shipping", 500), ("discount", -300)])
        self.assertEqual(result["1001"]["purchaseLines"][0]["vendorStatus"], "matched")
        self.assertEqual(result["1002"]["kind"], "invoice")
        self.assertEqual(result["1002"]["customer"]["match"], "new")
        self.assertEqual(result["1003"], {"orderId": "1003", "status": "review",
                                          "reason": "refund_or_cancellation_requires_reconciliation"})
        self.assertEqual(result["1004"]["reason"], "taxable_order_requires_manual_entry")

    def test_lines_reconcile_to_the_cent(self):
        for o in plan()["orders"]:
            if o["status"] == "planned":
                self.assertEqual(sum(l["amountCents"] for l in o["lines"]), o["totalCents"])

    def test_plan_never_describes_provider_writes(self):
        text = json.dumps(plan())
        for word in ["operations", "POST", "requestid", "create"]:
            self.assertNotIn(word, text)
        self.assertIn("No QuickBooks or BigCommerce writes", plan()["summary"])


class Rejections(unittest.TestCase):
    def test_total_mismatch_and_non_cent_amounts_go_to_review(self):
        os = orders()
        os[1]["total_inc_tax"] = "20.01"
        os[0]["shipping_cost_ex_tax"] = "5.005"
        result = by_id(plan(os=os))
        self.assertEqual(result["1002"]["reason"], "order_total_does_not_reconcile")
        self.assertEqual(result["1001"]["reason"], "missing_or_non_cent_order_amount")

    def test_discount_cannot_go_on_an_import_invoice(self):
        os = orders()
        os[0]["payment_status"] = "pending"
        self.assertEqual(by_id(plan(os=os))["1001"]["reason"], "discount_not_supported_by_invoice_import")

    def test_unmapped_sku_and_ineligible_status(self):
        os = orders()
        os[1]["products"][0]["sku"] = "UNKNOWN"
        os[0]["status_id"] = 1
        result = by_id(plan(os=os))
        self.assertEqual(result["1002"]["reason"], "item_mapping_required")
        self.assertEqual(result["1001"]["reason"], "ineligible_order_status")
        safe = by_id(plan())
        self.assertEqual(safe["1002"]["status"], "planned")

    def test_formula_like_buyer_name_is_review_not_rewritten(self):
        os = orders()
        os[1]["billing_address"]["first_name"] = "=HYPERLINK(1)"
        os[1]["billing_address"]["company"] = ""
        result = by_id(plan(os=os))
        self.assertEqual(result["1002"]["reason"], "customer_name_requires_review")
        self.assertEqual(result["1001"]["status"], "planned")

    def test_duplicate_and_conflicting_snapshots(self):
        os = orders()
        twin = copy.deepcopy(os[0])
        self.assertEqual([o.get("reason") for o in plan(os=[os[0], twin])["orders"]], [None, "duplicate_order_in_batch"])
        twin["total_inc_tax"] = "99.00"
        self.assertEqual({o["reason"] for o in plan(os=[os[0], twin])["orders"]}, {"conflicting_order_snapshots"})


class Customers(unittest.TestCase):
    def test_ambiguous_email_is_review(self):
        customers = copy.deepcopy(FIXTURE["quickbooks"]["customers"])
        customers.append({"Id": "11", "DisplayName": "Example Buyer 2", "PrimaryEmailAddr": {"Address": "BUYER@example.test"}})
        self.assertEqual(by_id(plan(customers=customers))["1001"]["reason"], "ambiguous_customer_email")

    def test_inactive_and_collision_and_disallowed_new(self):
        inactive = copy.deepcopy(FIXTURE["quickbooks"]["customers"])
        inactive[0]["Active"] = False
        self.assertEqual(by_id(plan(customers=inactive))["1001"]["reason"], "inactive_customer")
        collision = copy.deepcopy(FIXTURE["quickbooks"]["customers"]) + [{"Id": "12", "DisplayName": "Pat Sample"}]
        self.assertEqual(by_id(plan(customers=collision))["1002"]["reason"], "customer_name_collision")
        self.assertEqual(by_id(plan(c=config(allowNewCustomers=False)))["1002"]["reason"], "customer_not_found")

    def test_incomplete_customer_list_is_unknown_not_new(self):
        self.assertEqual(by_id(plan(complete=False))["1002"]["reason"], "qbo_customer_list_incomplete")

    def test_without_quickbooks_customers_are_unverified(self):
        result = by_id(plan(customers=None, vendors=None))
        self.assertEqual(result["1001"]["customer"]["match"], "unverified")
        self.assertEqual(result["1001"]["purchaseLines"][0]["vendorStatus"], "unverified")
        self.assertEqual(plan(customers=None)["customerLookup"], "none")

    def test_two_buyers_cannot_share_a_new_name(self):
        os = orders()
        os[0]["billing_address"].update({"first_name": "Pat", "last_name": "Sample"})
        result = by_id(plan(os=os, customers=None, vendors=None))
        self.assertEqual({result["1001"]["reason"], result["1002"]["reason"]}, {"new_customer_name_conflict"})

    def test_one_email_cannot_become_two_new_customers(self):
        os = orders()
        os[0]["billing_address"].update({"first_name": "Pat", "last_name": "Other", "email": "PAT@example.test"})
        os[1]["billing_address"].update({"email": "pat@example.test"})
        result = by_id(plan(os=os, customers=None, vendors=None))
        self.assertEqual({result["1001"]["reason"], result["1002"]["reason"]}, {"new_customer_email_conflict"})
        self.assertEqual(result["1004"]["reason"], "taxable_order_requires_manual_entry")

    def test_same_email_and_name_is_one_customer(self):
        os = orders()
        os[0]["billing_address"].update({"first_name": "Pat", "last_name": "Sample", "email": "pat@example.test", "company": ""})
        os[1]["billing_address"].update({"email": "PAT@example.test"})
        os[0]["payment_status"] = "pending"
        os[0]["discount_amount"] = "0.00"
        os[0]["total_ex_tax"] = "25.00"
        os[0]["total_inc_tax"] = "25.00"
        result = by_id(plan(os=os, customers=None, vendors=None))
        self.assertEqual(result["1001"]["status"], "planned")
        self.assertEqual(result["1002"]["status"], "planned")

    def test_vendor_not_found(self):
        self.assertEqual(by_id(plan(vendors=[]))["1001"]["purchaseLines"][0]["vendorStatus"], "not_found_in_qbo")


class ConfigAndIpc(unittest.TestCase):
    def test_invalid_configs(self):
        self.assertEqual(main.plan({"config": config(eligibleStatusIds=[4]), "orders": []})["reason"], "invalid_config")
        self.assertEqual(main.plan({"config": config(region="non_US"), "orders": []})["reason"], "zero_tax_code_required_for_non_us")
        self.assertEqual(main.plan({"config": config(), "orders": [{}] * (main.MAX_ORDERS + 1)})["reason"], "bounded_orders_required")

    def test_stdin_stdout_contract(self):
        data = json.dumps({"config": config(), "orders": orders(), "customers": None, "vendors": None})
        done = subprocess.run([sys.executable, "-I", str(HERE / "main.py")], input=data, capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, check=False)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout)["counts"], {"planned": 2, "review": 2})
        bad = subprocess.run([sys.executable, "-I", str(HERE / "main.py")], input="{", capture_output=True, text=True, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(json.loads(bad.stdout)["reason"], "invalid_json")


if __name__ == "__main__":
    unittest.main()
