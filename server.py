"""
FitManager Onboarding Dashboard — Live API Server
Fetches real-time data from Zoho CRM and serves the dashboard.

Usage:
    python server.py              # Starts on port 8080
    python server.py --port 9000  # Custom port
    python server.py --snapshot   # Generate static HTML snapshot (no server)
"""

import json
import os
import sys
import time
import subprocess
import argparse
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory, send_file

# ── Config ──
CREDS_PATH = os.path.expanduser("~/.hermes/credentials/zoho-crm.json")
CRM_ORG = "904566118"
API_BASE = "https://www.zohoapis.com/crm/v6"
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=DASHBOARD_DIR)

# ── Token Management ──
_token_cache = {"token": None, "expires_at": 0}

def get_access_token():
    """Refresh Zoho OAuth token (cached for 45 min)."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]

    creds = json.load(open(CREDS_PATH))
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://accounts.zoho.com/oauth/v2/token",
        "-d", f"refresh_token={creds['refresh_token']}",
        "-d", f"client_id={creds['client_id']}",
        "-d", f"client_secret={creds['client_secret']}",
        "-d", "grant_type=refresh_token"
    ], capture_output=True, text=True, timeout=30)

    data = json.loads(result.stdout)
    token = data.get("access_token")
    if not token:
        raise Exception(f"Token refresh failed: {data.get('error_description', 'unknown')}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + 2700  # 45 min cache
    return token


def zoho_get(module, params=None):
    """GET request to Zoho CRM API with pagination."""
    token = get_access_token()
    url = f"{API_BASE}/{module}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())

    result = subprocess.run([
        "curl", "-s",
        "-H", f"Authorization: Zoho-oauthtoken {token}",
        "-H", f"X-CRM-ORG: {CRM_ORG}",
        url
    ], capture_output=True, text=True, timeout=30)

    if not result.stdout.strip():
        return []

    data = json.loads(result.stdout)
    records = data.get("data", [])

    # Handle pagination
    info = data.get("info", {})
    page = 1
    while info.get("more_records") and info["more_records"] is True:
        page += 1
        p = dict(params) if params else {}
        p["page"] = page
        next_url = f"{API_BASE}/{module}?" + "&".join(f"{k}={v}" for k, v in p.items())
        r2 = subprocess.run([
            "curl", "-s",
            "-H", f"Authorization: Zoho-oauthtoken {token}",
            "-H", f"X-CRM-ORG: {CRM_ORG}",
            next_url
        ], capture_output=True, text=True, timeout=30)
        if not r2.stdout.strip():
            break
        d2 = json.loads(r2.stdout)
        records.extend(d2.get("data", []))
        info = d2.get("info", {})

    return records


# ── Risk Calculation ──
PHASES = ['Kick Off Call', 'Phase 1', 'Phase 2', 'Phase 3', 'Additional Training', 'LIVE']
PHASE_WEIGHT = {'Kick Off Call': 5, 'Phase 1': 20, 'Phase 2': 45, 'Phase 3': 65, 'Additional Training': 80, 'LIVE': 100}

def calculate_risk(customer):
    """Calculate risk status from available data."""
    stage = customer.get("stage", "")
    if stage == "LIVE":
        return "Completed"

    go_live = customer.get("goLive")
    created = customer.get("created")
    now = datetime.now(timezone.utc)

    if go_live:
        try:
            gl = datetime.fromisoformat(go_live.replace("Z", "+00:00"))
            if gl < now:
                return "Overdue"
        except:
            pass

    if created:
        try:
            cr = datetime.fromisoformat(created.replace("Z", "+00:00"))
            days_active = (now - cr).days
            phase_weight = PHASE_WEIGHT.get(stage, 0)

            if days_active > 60 and phase_weight < 30:
                return "Stalled"
            if days_active > 45 and phase_weight < 45:
                return "At Risk"

            if go_live:
                try:
                    gl = datetime.fromisoformat(go_live.replace("Z", "+00:00"))
                    days_to_go = (gl - now).days
                    if days_to_go < 14 and phase_weight < 65:
                        return "At Risk"
                    if days_to_go < 7 and phase_weight < 80:
                        return "Overdue"
                except:
                    pass
        except:
            pass

    return "On Track"


def get_progress(stage):
    return PHASE_WEIGHT.get(stage, 0)


# ── Data Fetching ──
def fetch_onboarding_data():
    """Fetch all active onboardings with related data."""
    # Fields to fetch from Onboarding_Module
    fields = ",".join([
        "Name", "Onboarding_Stage", "Service_Type", "Industry",
        "Account_Name", "Deal_Look_Up", "Primary_Contact",
        "Target_Go_Live_Date", "Actual_Go_Live_Date",
        "Training_Status", "Training_Completion", "Training_Program",
        "GHL_Training_Phase", "GHL_Training_Step", "Training_Portal_Accessed",
        "Welcome_Kit_Status", "Welcome_Kit_Sent_Date_and_Time",
        "Risk_Status", "Health_Score", "Primary_Blocker", "Blocker_Type",
        "Is_Waiting_On_FitManager", "Is_Waiting_On_Customer",
        "Total_Tasks_Count", "Completed_Tasks_Count",
        "Phase_1_Start_Date", "Phase_1_End_Date",
        "Phase_2_Start_Date", "Phase_2_End_Date",
        "Phase_3_Start_Date", "Phase_3_End_Date",
        "Last_Internal_Activity", "Last_Customer_Interaction",
        "Created_Time", "Modified_Time", "Owner"
    ])

    print("[API] Fetching Onboarding_Module records...")
    records = zoho_get("Onboarding_Module", {"fields": fields, "per_page": "200"})
    print(f"[API] Got {len(records)} records")

    # Filter out LIVE customers (dashboard shows active only)
    active = [r for r in records if r.get("Onboarding_Stage") != "LIVE"]
    print(f"[API] Active (not LIVE): {len(active)}")

    # Fetch timeline events
    print("[API] Fetching Onboarding_Events...")
    event_fields = ",".join([
        "Name", "Event_Type", "Event_Timestamp", "Event_Source",
        "Event_Description", "Event_Actor", "Onboarding_Record", "Related_Record_ID"
    ])
    events = zoho_get("Onboarding_Events", {"fields": event_fields, "per_page": "200"})
    print(f"[API] Got {len(events)} timeline events")

    # Group events by onboarding record ID
    events_by_record = {}
    for ev in events:
        ob_ref = ev.get("Onboarding_Record")
        if ob_ref and isinstance(ob_ref, dict):
            rid = ob_ref.get("id", "")
            if rid not in events_by_record:
                events_by_record[rid] = []
            events_by_record[rid].append(ev)

    # Build customer list
    customers = []
    for r in active:
        # Extract contact name/email from Primary_Contact lookup
        contact_name = ""
        contact_email = ""
        pc = r.get("Primary_Contact")
        if pc and isinstance(pc, dict):
            contact_name = pc.get("name", "")

        account_name = ""
        acc = r.get("Account_Name")
        if acc and isinstance(acc, dict):
            account_name = acc.get("name", "")

        owner_name = ""
        owner = r.get("Owner")
        if owner and isinstance(owner, dict):
            owner_name = owner.get("name", "")

        stage = r.get("Onboarding_Stage", "")
        created = r.get("Created_Time", "")
        go_live = r.get("Target_Go_Live_Date", "")
        if go_live and len(go_live) == 10:  # date only, add time
            go_live += "T00:00:00-05:00"

        c = {
            "id": r.get("id", ""),
            "name": account_name or r.get("Name", "Unknown"),
            "stage": stage,
            "service": r.get("Service_Type", ""),
            "industry": r.get("Industry", ""),
            "contact": contact_name,
            "email": contact_email,
            "goLive": go_live,
            "created": created,
            "modified": r.get("Modified_Time", ""),
            "owner": owner_name,
            "training": r.get("Training_Status", ""),
            "trainingProg": r.get("Training_Program", ""),
            "trainingCompletion": r.get("Training_Completion"),
            "ghlPhase": r.get("GHL_Training_Phase", ""),
            "ghlStep": r.get("GHL_Training_Step", ""),
            "portalAccessed": r.get("Training_Portal_Accessed", False),
            "wkStatus": r.get("Welcome_Kit_Status", ""),
            "wkSentAt": r.get("Welcome_Kit_Sent_Date_and_Time", ""),
            "riskStatus": r.get("Risk_Status", ""),
            "healthScore": r.get("Health_Score"),
            "primaryBlocker": r.get("Primary_Blocker", ""),
            "blockerType": r.get("Blocker_Type", ""),
            "waitingOnUs": r.get("Is_Waiting_On_FitManager", False),
            "waitingOnCustomer": r.get("Is_Waiting_On_Customer", False),
            "totalTasks": r.get("Total_Tasks_Count"),
            "completedTasks": r.get("Completed_Tasks_Count"),
            "phase1Start": r.get("Phase_1_Start_Date", ""),
            "phase1End": r.get("Phase_1_End_Date", ""),
            "phase2Start": r.get("Phase_2_Start_Date", ""),
            "phase2End": r.get("Phase_2_End_Date", ""),
            "phase3Start": r.get("Phase_3_Start_Date", ""),
            "phase3End": r.get("Phase_3_End_Date", ""),
            "lastInternalActivity": r.get("Last_Internal_Activity", ""),
            "lastCustomerInteraction": r.get("Last_Customer_Interaction", ""),
            # Timeline events (from Onboarding_Events module)
            "timelineEvents": [],
        }

        # Attach timeline events for this record
        record_id = r.get("id", "")
        if record_id in events_by_record:
            c["timelineEvents"] = [
                {
                    "type": ev.get("Event_Type", ""),
                    "timestamp": ev.get("Event_Timestamp", ""),
                    "description": ev.get("Event_Description", ""),
                    "source": ev.get("Event_Source", ""),
                    "actor": ev.get("Event_Actor", ""),
                    "relatedRecordId": ev.get("Related_Record_ID", ""),
                }
                for ev in events_by_record[record_id]
            ]

        # Compute derived fields
        c["risk"] = c["riskStatus"] or calculate_risk(c)
        c["progress"] = get_progress(stage)

        if created:
            try:
                cr = datetime.fromisoformat(created.replace("Z", "+00:00"))
                c["daysActive"] = (datetime.now(timezone.utc) - cr).days
            except:
                c["daysActive"] = 0
        else:
            c["daysActive"] = 0

        customers.append(c)

    # Sort by risk severity then days active
    risk_order = {"Overdue": 0, "Stalled": 1, "At Risk": 2, "On Track": 3, "Completed": 4}
    customers.sort(key=lambda c: (risk_order.get(c["risk"], 5), -c["daysActive"]))

    return customers


# ── Flask Routes ──
@app.route("/")
def index():
    return send_file(os.path.join(DASHBOARD_DIR, "index.html"))


@app.route("/api/customers")
def api_customers():
    try:
        customers = fetch_onboarding_data()
        return jsonify({
            "success": True,
            "data": customers,
            "count": len(customers),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "data": [],
            "count": 0
        }), 500


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FitManager Onboarding Dashboard Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--snapshot", action="store_true", help="Generate static snapshot and exit")
    args = parser.parse_args()

    if args.snapshot:
        print("[Snapshot] Fetching live data from Zoho CRM...")
        customers = fetch_onboarding_data()
        snapshot_path = os.path.join(DASHBOARD_DIR, "snapshot.json")
        with open(snapshot_path, "w") as f:
            json.dump({"data": customers, "count": len(customers), "timestamp": datetime.now(timezone.utc).isoformat()}, f, indent=2)
        print(f"[Snapshot] Wrote {len(customers)} customers to {snapshot_path}")
        sys.exit(0)

    print(f"[Server] Starting on http://{args.host}:{args.port}")
    print(f"[Server] Dashboard: http://localhost:{args.port}")
    print(f"[Server] API: http://localhost:{args.port}/api/customers")
    app.run(host=args.host, port=args.port, debug=False)
