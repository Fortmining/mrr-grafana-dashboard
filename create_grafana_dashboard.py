#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing required environment variable: {name}")
    return value

def request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "mrr-grafana-dashboard/1.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc

def main() -> None:
    grafana_url = env("GRAFANA_URL").rstrip("/")
    token = env("GRAFANA_TOKEN")
    infinity_name = env("GRAFANA_INFINITY_NAME", "mrr-pages")
    pages_base = env("PAGES_BASE_URL")
    dashboard_title = os.getenv("DASHBOARD_TITLE", "MRR Mining Command Center")

    ds = request("GET", f"{grafana_url}/api/datasources/name/{infinity_name}", token)
    datasource_uid = ds["uid"]

    model = json.loads(Path("dashboard_template.json").read_text())

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    node[k] = v.replace("https://example.com", pages_base.rstrip("/"))
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(model)
    model["title"] = dashboard_title
    for item in model.get("templating", {}).get("list", []):
        if item.get("name") == "datasource":
            item["current"] = {"selected": True, "text": infinity_name, "value": datasource_uid}

    payload = {
        "dashboard": model,
        "overwrite": True,
        "message": "Create/update MRR Mining Command Center",
    }

    result = request("POST", f"{grafana_url}/api/dashboards/db", token, payload)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
