#!/usr/bin/env python3
"""FitManager Agent Dashboard API Server

Serves the dashboard HTML and provides REST API endpoints for
managing Hermes cron jobs (list, pause, resume, trigger).
"""

import http.server
import json
import subprocess
import os
import re
from urllib.parse import urlparse

PORT = 8888
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
HERMES_BIN = os.path.expanduser("~/.local/bin/hermes")


def run_hermes_cron(args: list) -> dict:
    """Run a hermes cron command and return parsed output."""
    cmd = [HERMES_BIN, "cron"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "HERMES_ACCEPT_HOOKS": "1"}
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Command timed out"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


def parse_cron_list(raw: str) -> list:
    """Parse hermes cron list output into structured data."""
    jobs = []
    current = {}

    for line in raw.split("\n"):
        # Match job header: "  job_id [state]"
        header = re.match(r'^\s+(\w+)\s+\[(\w+)\]', line)
        if header:
            if current:
                jobs.append(current)
            current = {
                "job_id": header.group(1),
                "state": header.group(2),
                "enabled": header.group(2) != "paused",
            }
            continue

        # Match fields
        if current:
            for field in ["Name", "Schedule", "Next run", "Deliver", "Skills", "Last run", "Script", "Mode"]:
                m = re.match(rf'^\s+{field}:\s+(.*)', line)
                if m:
                    val = m.group(1).strip()
                    key = field.lower().replace(" ", "_")
                    if field == "Last run":
                        # Parse "2026-08-13T08:30:19  ok" -> timestamp + status
                        parts = val.rsplit("  ", 1)
                        current["last_run"] = parts[0].strip() if parts else val
                        current["last_status"] = parts[1].strip() if len(parts) > 1 else ""
                    else:
                        current[key] = val

    if current:
        jobs.append(current)

    return jobs


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/jobs":
            self._handle_list_jobs()
        elif re.match(r'^/api/jobs/([^/]+)/pause$', path):
            job_id = path.split("/")[3]
            self._handle_action("pause", job_id)
        elif re.match(r'^/api/jobs/([^/]+)/resume$', path):
            job_id = path.split("/")[3]
            self._handle_action("resume", job_id)
        elif re.match(r'^/api/jobs/([^/]+)/run$', path):
            job_id = path.split("/")[3]
            self._handle_action("run", job_id)
        elif path == "" or path == "/":
            self.path = "/agent-dashboard.html"
            super().do_GET()
        else:
            super().do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_list_jobs(self):
        result = run_hermes_cron(["list", "--all"])
        jobs = parse_cron_list(result["stdout"]) if result["success"] else []
        self._json_response({"jobs": jobs, "count": len(jobs)})

    def _handle_action(self, action: str, job_id: str):
        result = run_hermes_cron([action, job_id])
        self._json_response({
            "action": action,
            "job_id": job_id,
            "success": result["success"],
            "message": result["stdout"] or result["stderr"],
        })

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Dashboard API on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
