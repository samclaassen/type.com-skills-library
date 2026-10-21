#!/usr/bin/env python3
"""
Minimal mock of the Klaviyo endpoints scripts/klaviyo_fetch.py uses, served
from examples/data/bundle.json. It checks auth and revision headers, paginates
flows, events and the flow report, and returns one 429 (Retry-After: 0) so
retry handling is exercised.

    python3 tests/mock_klaviyo.py PORT
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "examples", "data", "bundle.json"), encoding="utf-8") as f:
    B = json.load(f)

KEY = "pk_test_mock"
METRICS = [("M_PO", "Placed Order"), ("M_BE", "Bounced Email"), ("M_AOS", "Active on Site"), ("M_VP", "Viewed Product")]
PROFILES = {f"P{i:04d}": ev for i, ev in enumerate(B["bounces"])}
STATE = {"throttled": False}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/vnd.api+json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def guard(self):
        if self.headers.get("Authorization") != f"Klaviyo-API-Key {KEY}":
            self.send(401, {"errors": [{"detail": "bad key"}]})
            return False
        if not self.headers.get("revision"):
            self.send(400, {"errors": [{"detail": "missing revision"}]})
            return False
        return True

    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}/api"

    def do_GET(self):
        if not self.guard():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path[len("/api"):]
        if path == "/metrics":
            return self.send(200, {"data": [{"type": "metric", "id": i, "attributes": {"name": n}} for i, n in METRICS],
                                   "links": {"next": None}})
        if path == "/flows":
            page = int(q.get("page", ["0"])[0])
            flows = B["flows"] + [{"id": "ArchX1", "name": "Old archived flow", "status": "draft",
                                   "trigger_type": "Metric", "archived": True}]
            chunk = flows[page * 3:(page + 1) * 3]
            nxt = f"{self.base()}/flows?page={page + 1}" if (page + 1) * 3 < len(flows) else None
            return self.send(200, {"data": [{"type": "flow", "id": f["id"], "attributes": {
                "name": f["name"], "status": f["status"], "trigger_type": f["trigger_type"],
                "archived": f.get("archived", False)}} for f in chunk], "links": {"next": nxt}})
        if path.startswith("/flow-messages/"):
            mid = path.rsplit("/", 1)[1]
            m = next((m for m in B["flow_messages"] if m["id"] == mid), None)
            if not m:
                return self.send(404, {"errors": [{"detail": "not found"}]})
            return self.send(200, {"data": {"type": "flow-message", "id": mid, "attributes": {"name": m["name"]}}})
        if path == "/events":
            flt = q.get("filter", [""])[0]
            if '"M_AOS"' in flt:
                pid = flt.split('equals(profile_id,"')[1].split('"')[0]
                active = PROFILES.get(pid, {}).get("active_on_site")
                data = [{"type": "event", "id": f"E_{pid}", "attributes": {"datetime": "2026-09-01T00:00:00+00:00"}}] \
                    if active else []
                return self.send(200, {"data": data, "links": {"next": None}})
            if '"M_BE"' in flt:
                if not STATE["throttled"]:
                    STATE["throttled"] = True
                    return self.send(429, {"errors": [{"detail": "throttled"}]}, {"Retry-After": "0"})
                page = int(q.get("page", ["0"])[0])
                ids = list(PROFILES)[page * 120:(page + 1) * 120]
                data, included = [], []
                for pid in ids:
                    ev = PROFILES[pid]
                    data.append({"type": "event", "id": f"B_{pid}", "attributes": {
                        "datetime": ev["datetime"],
                        "event_properties": {"Bounce Type": ev["bounce_type"], "$flow": ev["flow_id"],
                                             "$message": "x", "Subject": "Did you forget something?"}},
                        "relationships": {"profile": {"data": {"type": "profile", "id": pid}}}})
                    included.append({"type": "profile", "id": pid, "attributes": {
                        "email": ev["email"], "created": ev["profile_created"]}})
                nxt = None
                if (page + 1) * 120 < len(PROFILES):
                    nq = dict((k, v[0]) for k, v in q.items())
                    nq["page"] = str(page + 1)
                    nxt = f"{self.base()}/events?{urllib.parse.urlencode(nq)}"
                return self.send(200, {"data": data, "included": included, "links": {"next": nxt}})
        return self.send(404, {"errors": [{"detail": f"no mock for GET {path}"}]})

    def do_POST(self):
        if not self.guard():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/flow-series-reports":
            return self.send(404, {"errors": [{"detail": "no mock"}]})
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        attrs = body["data"]["attributes"]
        if attrs.get("conversion_metric_id") != "M_PO" or set(attrs["statistics"]) - {"recipients", "bounced"}:
            return self.send(400, {"errors": [{"detail": "bad report request"}]})
        series = B["weekly"] if attrs["interval"] == "weekly" else B["daily"]
        cursor = int(urllib.parse.parse_qs(u.query).get("page_cursor", ["0"])[0])
        rows = series["rows"][cursor * 4:(cursor + 1) * 4]
        results = [{"groupings": {"flow_id": r["flow_id"], "send_channel": "email",
                                  "flow_message_id": r["flow_message_id"]},
                    "statistics": {"recipients": r["recipients"], "bounced": r["bounced"]}} for r in rows]
        if cursor == 0:  # an SMS row the fetcher must ignore
            results.append({"groupings": {"flow_id": "RtA9cK", "send_channel": "sms", "flow_message_id": "SMS1"},
                            "statistics": {"recipients": [5] * len(series["date_times"]),
                                           "bounced": [5] * len(series["date_times"])}})
        nxt = f"{self.base()}/flow-series-reports?page_cursor={cursor + 1}" \
            if (cursor + 1) * 4 < len(series["rows"]) else None
        return self.send(200, {"data": {"type": "flow-series-report", "attributes": {
            "date_times": series["date_times"], "results": results}}, "links": {"next": nxt}})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
