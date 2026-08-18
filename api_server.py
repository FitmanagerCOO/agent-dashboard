#!/usr/bin/env python3
"""
Onboarding Dashboard API Server
Queries Zoho CRM (Onboarding_Module + Onboarding_Events) and serves
data for the onboarding dashboard frontend.

Endpoints:
  GET /api/customers — Returns all active onboarding customers with timeline events
  GET /              — Serves the static HTML dashboard
  GET /health        — Health check
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# ── Config ──
PORT = 8090
CREDS_PATH = os.path.expanduser("~/.hermes/credentials/zoho-crm.json")
CRM_ORG = "904566118"
BASE_URL = "https://www.zohoapis.com/crm/v2"
DASHBOARD_DIR = Path(__file__).parent

# Token cache
_token_cache = {"token": None, "expires": 0}


def get_access_token():
    """Get Zoho CRM access token, cached for 45 minutes."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    creds = json.loads(open(CREDS_PATH).read())
    data = urllib.parse.urlencode({
        "refresh_token": creds["refresh_token"],
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        "https://accounts.zoho.com/oauth/v2/token",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    token = result["access_token"]
    _token_cache["token"] = token
    _token_cache["expires"] = now + 2700  # 45 min cache
    print(f"[API] Token refreshed at {datetime.now().strftime('%H:%M:%S')}", flush=True)
    return token


def zoho_get(path, params=None):
    """Make authenticated GET request to Zoho CRM API."""
    token = get_access_token()
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Zoho-oauthtoken {token}")
    req.add_header("X-CRM-ORG", CRM_ORG)

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
            if not body.strip():
                return None
            return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        print(f"[API] HTTP {e.code} for {path}: {e.read().decode()[:200]}", flush=True)
        raise


def fetch_all_records(module, fields=None, per_page=200):
    """Fetch all records from a Zoho module with pagination."""
    all_records = []
    page = 1
    while True:
        params = {"per_page": per_page, "page": page}
        if fields:
            params["fields"] = ",".join(fields)
        result = zoho_get(f"/{module}", params)
        if not result or "data" not in result:
            break
        all_records.extend(result["data"])
        if not result.get("info", {}).get("more_records", False):
            break
        page += 1
    return all_records


def fetch_contact_email(contact_id):
    """Fetch contact email by ID."""
    try:
        result = zoho_get(f"/Contacts/{contact_id}")
        if result and "data" in result and result["data"]:
            return result["data"][0].get("Email", "")
    except Exception:
        pass
    return ""


def batch_fetch_contacts(contact_ids):
    """Fetch multiple contacts in one call using GET /Contacts?ids=..."""
    if not contact_ids:
        return {}
    # Zoho supports up to 100 IDs per batch
    contact_map = {}
    for i in range(0, len(contact_ids), 100):
        batch = contact_ids[i:i+100]
        ids_str = ",".join(batch)
        try:
            result = zoho_get("/Contacts", {"ids": ids_str, "fields": "id,Email,Phone,Full_Name"})
            if result and "data" in result:
                for c in result["data"]:
                    contact_map[c["id"]] = {
                        "email": c.get("Email", ""),
                        "phone": c.get("Phone", ""),
                        "name": c.get("Full_Name", ""),
                    }
        except Exception as e:
            print(f"[API] Batch contact fetch error: {e}", flush=True)
    return contact_map


def batch_fetch_deals(deal_ids):
    """Fetch multiple deals in one call using GET /Deals?ids=..."""
    if not deal_ids:
        return {}
    deal_map = {}
    for i in range(0, len(deal_ids), 100):
        batch = deal_ids[i:i+100]
        ids_str = ",".join(batch)
        try:
            result = zoho_get("/Deals", {"ids": ids_str, "fields": "id,Deal_Name,Closing_Date,Sign_Up_Date,Stage"})
            if result and "data" in result:
                for d in result["data"]:
                    deal_map[d["id"]] = {
                        "name": d.get("Deal_Name", ""),
                        "closingDate": d.get("Closing_Date", ""),
                        "signUpDate": d.get("Sign_Up_Date", ""),
                        "stage": d.get("Stage", ""),
                    }
        except Exception as e:
            print(f"[API] Error batch-fetching deals: {e}", flush=True)
    return deal_map


def fetch_onboarding_events():
    """Fetch all Onboarding_Events records and group by onboarding record ID."""
    events = fetch_all_records("Onboarding_Events", fields=[
        "Event_Type", "Event_Timestamp", "Event_Source",
        "Event_Description", "Event_Actor", "Onboarding_Record",
        "Related_Record_ID"
    ])
    grouped = {}
    for ev in events:
        ob_ref = ev.get("Onboarding_Record")
        if ob_ref and ob_ref.get("id"):
            ob_id = ob_ref["id"]
            if ob_id not in grouped:
                grouped[ob_id] = []
            grouped[ob_id].append({
                "type": ev.get("Event_Type", ""),
                "timestamp": ev.get("Event_Timestamp", ""),
                "source": ev.get("Event_Source", ""),
                "description": ev.get("Event_Description", ""),
                "actor": ev.get("Event_Actor", ""),
                "relatedRecordId": ev.get("Related_Record_ID", ""),
            })
    return grouped


def transform_customer(record, contact_map, events_map, deal_map):
    """Transform a Zoho CRM record into the dashboard data format."""
    # Extract lookup names
    account = record.get("Account_Name") or {}
    contact_ref = record.get("Primary_Contact") or {}
    deal = record.get("Deal_Look_Up") or {}

    # Get contact details from batch fetch
    contact_id = contact_ref.get("id", "")
    contact_info = contact_map.get(contact_id, {})

    # Get deal details from batch fetch
    deal_id = deal.get("id", "")
    deal_info = deal_map.get(deal_id, {})

    # Get events for this record
    ob_id = record.get("id", "")
    timeline_events = events_map.get(ob_id, [])

    # Map service type to short form
    svc = record.get("Service_Type", "") or ""
    if "V2" in svc:
        service = "V2"
    elif "V1" in svc or "Platform" in svc:
        service = "V1"
    else:
        service = svc

    # Map industry
    industry = record.get("Industry", "") or ""

    # Map training status from GHL fields
    ghl_phase = record.get("GHL_Training_Phase", "") or ""
    ghl_step = record.get("GHL_Training_Step", "") or ""
    training_status = record.get("Training_Status", "") or ""
    training_prog = record.get("Training_Program", "") or ""
    portal_accessed = record.get("Training_Portal_Accessed", False)
    training_completion = record.get("Training_Completion")

    return {
        "id": ob_id,
        "name": record.get("Name", "") or account.get("name", "Unknown"),
        "stage": record.get("Onboarding_Stage", "") or "Unassigned",
        "service": service,
        "contact": contact_ref.get("name", "") or contact_info.get("name", ""),
        "email": contact_info.get("email", ""),
        "phone": contact_info.get("phone", ""),
        "goLive": record.get("Target_Go_Live_Date", "") or "",
        "actualGoLive": record.get("Actual_Go_Live_Date", "") or "",
        "closedWonDate": deal_info.get("closingDate", ""),
        "training": training_status,
        "trainingProg": training_prog,
        "trainingCompletion": training_completion,
        "ghlPhase": ghl_phase,
        "ghlStep": ghl_step,
        "portalAccessed": portal_accessed,
        "industry": industry,
        "wkStatus": record.get("Welcome_Kit_Status", "") or "",
        "wkSentAt": record.get("Welcome_Kit_Sent_Date_and_Time", "") or "",
        "created": record.get("Created_Time", "") or "",
        "lastActivity": record.get("Last_Activity_Time", "") or "",
        "lastInternalActivity": record.get("Last_Internal_Activity", "") or "",
        "lastCustomerInteraction": record.get("Last_Customer_Interaction", "") or "",
        "phase1Start": record.get("Phase_1_Start_Date", "") or "",
        "phase1End": record.get("Phase_1_End_Date", "") or "",
        "phase2Start": record.get("Phase_2_Start_Date", "") or "",
        "phase2End": record.get("Phase_2_End_Date", "") or "",
        "phase3Start": record.get("Phase_3_Start_Date", "") or "",
        "phase3End": record.get("Phase_3_End_Date", "") or "",
        "riskStatus": record.get("Risk_Status", "") or "",
        "healthScore": record.get("Health_Score"),
        "primaryBlocker": record.get("Primary_Blocker", "") or "",
        "blockerType": record.get("Blocker_Type", "") or "",
        "waitingOnFitManager": record.get("Is_Waiting_On_FitManager", False),
        "waitingOnCustomer": record.get("Is_Waiting_On_Customer", False),
        "totalTasks": record.get("Total_Tasks_Count"),
        "completedTasks": record.get("Completed_Tasks_Count"),
        "scaleAdded": record.get("Scale_Serial_Number_Added_To_SuperAdmin", False),
        "twilioAdded": record.get("Twilio_Number_Added", False),
        "accountId": account.get("id", ""),
        "accountName": account.get("name", ""),
        "dealId": deal_id,
        "dealName": deal.get("name", "") or deal_info.get("name", ""),
        "contactId": contact_id,
        "ownerName": (record.get("Owner") or {}).get("name", ""),
        "timelineEvents": timeline_events,
    }


# ── Cache ──
_cache = {"data": None, "expires": 0}
CACHE_TTL = 120  # 2 minutes


def get_customers_cached():
    """Get customers with 2-minute cache."""
    now = time.time()
    if _cache["data"] and now < _cache["expires"]:
        return _cache["data"]

    print("[API] Fetching fresh data from Zoho CRM...", flush=True)
    start = time.time()

    # Fetch all active onboarding records (exclude LIVE)
    fields = [
        "id", "Name", "Onboarding_Stage", "Service_Type",
        "Account_Name", "Deal_Look_Up", "Primary_Contact",
        "Target_Go_Live_Date", "Actual_Go_Live_Date",
        "Training_Status", "Training_Program", "Training_Completion",
        "GHL_Training_Phase", "GHL_Training_Step", "Training_Portal_Accessed",
        "Industry", "Welcome_Kit_Status", "Welcome_Kit_Sent_Date_and_Time",
        "Created_Time", "Last_Activity_Time",
        "Last_Internal_Activity", "Last_Customer_Interaction",
        "Phase_1_Start_Date", "Phase_1_End_Date",
        "Phase_2_Start_Date", "Phase_2_End_Date",
        "Phase_3_Start_Date", "Phase_3_End_Date",
        "Risk_Status", "Health_Score",
        "Primary_Blocker", "Blocker_Type",
        "Is_Waiting_On_FitManager", "Is_Waiting_On_Customer",
        "Total_Tasks_Count", "Completed_Tasks_Count",
        "Scale_Serial_Number_Added_To_SuperAdmin", "Twilio_Number_Added",
        "Owner",
    ]
    records = fetch_all_records("Onboarding_Module", fields=fields)

    # Filter out LIVE and Additional Training records
    active = [r for r in records if (r.get("Onboarding_Stage") or "") not in ("LIVE", "Additional Training in progress", "Additional Training completed", "Additional Training")]
    print(f"[API] Found {len(active)} active onboarding records ({len(records)} total)", flush=True)

    # Collect contact IDs for batch fetch
    contact_ids = []
    for r in active:
        ref = r.get("Primary_Contact") or {}
        cid = ref.get("id")
        if cid:
            contact_ids.append(cid)

    # Collect deal IDs for batch fetch
    deal_ids = []
    for r in active:
        ref = r.get("Deal_Look_Up") or {}
        did = ref.get("id")
        if did:
            deal_ids.append(did)

    # Batch fetch contacts
    contact_map = batch_fetch_contacts(contact_ids)
    print(f"[API] Fetched {len(contact_map)} contact details", flush=True)

    # Batch fetch deals
    deal_map = batch_fetch_deals(deal_ids)
    print(f"[API] Fetched {len(deal_map)} deal details", flush=True)

    # Fetch onboarding events
    events_map = fetch_onboarding_events()
    print(f"[API] Found events for {len(events_map)} onboarding records", flush=True)

    # Transform
    customers = [transform_customer(r, contact_map, events_map, deal_map) for r in active]

    elapsed = time.time() - start
    print(f"[API] Data ready in {elapsed:.1f}s — {len(customers)} customers", flush=True)

    _cache["data"] = customers
    _cache["expires"] = now + CACHE_TTL
    return customers


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the dashboard HTML and API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/api/customers":
            self._handle_customers()
        elif self.path == "/health":
            self._handle_health()
        else:
            # Serve static files (index.html, etc.)
            super().do_GET()

    def _handle_customers(self):
        try:
            customers = get_customers_cached()
            response = json.dumps({
                "success": True,
                "data": customers,
                "count": len(customers),
                "cached": _cache["expires"] > time.time(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            print(f"[API] Error: {e}", flush=True)
            error = json.dumps({"success": False, "error": str(e)})
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error.encode())

    def _handle_health(self):
        response = json.dumps({
            "status": "ok",
            "uptime": time.time(),
            "token_cached": _token_cache["token"] is not None,
            "data_cached": _cache["data"] is not None,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode())

    def log_message(self, format, *args):
        """Suppress default access logs for clean output."""
        if "/api/" in str(args[0]):
            print(f"[API] {args[0]}", flush=True)


def main():
    print(f"[Dashboard] Starting on http://localhost:{PORT}", flush=True)
    print(f"[Dashboard] Serving from {DASHBOARD_DIR}", flush=True)
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] Shutting down.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
