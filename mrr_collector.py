#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
import urllib.request
import urllib.error

API_BASE = "https://www.miningrigrentals.com/api/v2"


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def get_nonce() -> str:
    return str(int(time.time() * 1000000))


def sign_headers(api_key: str, api_secret: str, endpoint: str, nonce: str) -> dict[str, str]:
    endpoint_no_trailing = endpoint.rstrip("/")
    message = f"{api_key}{nonce}{endpoint_no_trailing}".encode("utf-8")
    signature = hmac.new(api_secret.encode("utf-8"), message, hashlib.sha1).hexdigest()
    return {
        "x-api-key": api_key,
        "x-api-nonce": nonce,
        "x-api-sign": signature,
        "Accept": "application/json",
        "User-Agent": "mrr-grafana-collector/1.0",
    }


def mrr_get(api_key: str, api_secret: str, endpoint: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{API_BASE}{endpoint}{query}"
    nonce = get_nonce()
    req = urllib.request.Request(url, headers=sign_headers(api_key, api_secret, endpoint, nonce), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} for {endpoint}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {endpoint}: {exc}") from exc

    if not payload.get("success", False):
        raise RuntimeError(f"MRR API error for {endpoint}: {payload}")
    return payload["data"]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "null"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "online", "rented"}
    return bool(value)


def parse_mrr_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_chart_pairs(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    payload = f"[{text}]"
    try:
        parsed = ast.literal_eval(payload)
    except Exception:
        return []
    results = []
    for item in parsed:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            ts_ms, value = item
            try:
                ts_iso = datetime.fromtimestamp(float(ts_ms) / 1000, tz=timezone.utc).isoformat()
                results.append({"time": ts_iso, "value": float(value)})
            except Exception:
                continue
    return results


def beginning_of_day(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def main() -> None:
    api_key = env("MRR_API_KEY")
    api_secret = env("MRR_API_SECRET")
    rig_ids = [x.strip() for x in env("MRR_RIG_IDS").split(",") if x.strip()]
    output_dir = Path(os.getenv("OUTPUT_DIR", "docs/data"))
    output_dir.mkdir(parents=True, exist_ok=True)

    rig_endpoint = f"/rig/{';'.join(rig_ids)}"
    rigs_data = mrr_get(api_key, api_secret, rig_endpoint)
    if isinstance(rigs_data, dict):
        rigs_data = [rigs_data]

    balance = mrr_get(api_key, api_secret, "/account/balance")
    active_rentals = mrr_get(api_key, api_secret, "/rental", {"type": "owner", "history": "false", "limit": 100})
    history_rentals = mrr_get(api_key, api_secret, "/rental", {"type": "owner", "history": "true", "limit": 200})
    tx_lookback = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
    transactions = mrr_get(
        api_key,
        api_secret,
        "/account/transactions",
        {"start": 0, "limit": 200, "time_greater_eq": tx_lookback},
    )

    active_rentals_by_rig: dict[str, dict[str, Any]] = {}
    for rental in active_rentals.get("rentals", []):
        rig = rental.get("rig", {}) or {}
        active_rentals_by_rig[str(rig.get("id"))] = rental

    history_rentals_list = history_rentals.get("rentals", [])

    earnings_by_day_and_rig: dict[tuple[str, str], float] = defaultdict(float)
    now = datetime.now(timezone.utc)
    tx_list = transactions.get("transactions", [])
    earnings_24h = 0.0
    earnings_7d = 0.0

    for tx in tx_list:
        if str(tx.get("rig")) not in rig_ids:
            continue
        amount = safe_float(tx.get("amount"))
        tx_type = str(tx.get("type", "")).lower()
        when = parse_mrr_time(tx.get("when"))
        if amount <= 0 or "credit" not in tx_type or when is None:
            continue
        day = beginning_of_day(when).date().isoformat()
        earnings_by_day_and_rig[(day, str(tx.get("rig")))] += amount
        if when >= now - timedelta(hours=24):
            earnings_24h += amount
        if when >= now - timedelta(days=7):
            earnings_7d += amount

    rigs_output: list[dict[str, Any]] = []
    hashrate_history: list[dict[str, Any]] = []

    total_util_7d = 0.0
    total_fallback_btc_day = 0.0
    total_mrr_btc_day = 0.0

    for rig in rigs_data:
        rig_id = str(rig.get("id"))
        name = os.getenv(f"RIG_{rig_id}_NAME", rig.get("name", f"Rig {rig_id}"))
        status = rig.get("status", {}) or {}
        hashrate = rig.get("hashrate", {}) or {}
        price = rig.get("price", {}) or {}
        btc_price = price.get("BTC", {}) or {}
        active_rental = active_rentals_by_rig.get(rig_id)
        fallback_btc_day = safe_float(os.getenv(f"RIG_{rig_id}_FALLBACK_BTC_DAY", "0"))
        total_fallback_btc_day += fallback_btc_day
        total_mrr_btc_day += safe_float(btc_price.get("hour")) * 24

        completed_7d = [
            r for r in history_rentals_list
            if str((r.get("rig") or {}).get("id")) == rig_id and parse_mrr_time(r.get("start")) and parse_mrr_time(r.get("end"))
        ]
        window_start = now - timedelta(days=7)
        rented_seconds_7d = 0.0
        for rental in completed_7d:
            start = parse_mrr_time(rental.get("start"))
            end = parse_mrr_time(rental.get("end"))
            if not start or not end:
                continue
            overlap_start = max(start, window_start)
            overlap_end = min(end, now)
            if overlap_end > overlap_start:
                rented_seconds_7d += (overlap_end - overlap_start).total_seconds()
        if active_rental:
            start = parse_mrr_time(active_rental.get("start"))
            end = parse_mrr_time(active_rental.get("end"))
            if start and end:
                overlap_start = max(start, window_start)
                overlap_end = min(end, now)
                if overlap_end > overlap_start:
                    rented_seconds_7d += (overlap_end - overlap_start).total_seconds()

        util_7d_pct = round((rented_seconds_7d / (7 * 24 * 3600)) * 100, 2)
        total_util_7d += util_7d_pct

        rig_graph = mrr_get(api_key, api_secret, f"/rig/{rig_id}/graph", {"hours": 168})
        chart = (rig_graph or {}).get("chartdata", {}) or {}
        graph_hashtype = (rig_graph or {}).get("hashtype", hashrate.get("last_5min", {}).get("type", ""))
        for point in parse_chart_pairs(chart.get("average")):
            hashrate_history.append({
                "time": point["time"],
                "rig_id": rig_id,
                "rig": name,
                "value": safe_float(point["value"]) / 1_000_000_000_000,
                "unit": graph_hashtype,
            })

        rigs_output.append({
            "id": rig_id,
            "name": name,
            "algorithm": rig.get("type"),
            "status_text": status.get("status"),
            "online": safe_bool(status.get("online")),
            "rented": safe_bool(status.get("rented")),
            "poolstatus": rig.get("poolstatus"),
            "region": rig.get("region"),
            "rpi": safe_float(rig.get("rpi")),
            "hashrate_last_5min": safe_float(((hashrate.get("last_5min") or {}).get("hash"))),
            "hashrate_last_15min": safe_float(((hashrate.get("last_15min") or {}).get("hash"))),
            "hashrate_last_30min": safe_float(((hashrate.get("last_30min") or {}).get("hash"))),
            "hashrate_unit": ((hashrate.get("last_5min") or {}).get("type")) or ((hashrate.get("advertised") or {}).get("type")),
            "advertised_hashrate": safe_float(((hashrate.get("advertised") or {}).get("hash"))),
            "advertised_unit": (hashrate.get("advertised") or {}).get("type"),
            "price_btc_day": safe_float(btc_price.get("price")),
            "price_btc_hour": safe_float(btc_price.get("hour")),
            "price_btc_enabled": safe_bool(btc_price.get("enabled")),
            "active_rental_id": active_rental.get("id") if active_rental else None,
            "active_rental_paid_btc": safe_float((active_rental or {}).get("price", {}).get("paid")),
            "active_rental_start": (active_rental or {}).get("start"),
            "active_rental_end": (active_rental or {}).get("end"),
            "utilization_7d_pct": util_7d_pct,
            "fallback_btc_day": fallback_btc_day,
            "mrr_vs_fallback_btc_day": round((safe_float(btc_price.get("hour")) * 24) - fallback_btc_day, 8),
        })

    wallet_btc = balance.get("BTC", {}) or {}

    latest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "wallet_btc_confirmed": safe_float(wallet_btc.get("confirmed")),
            "wallet_btc_pending": safe_float(wallet_btc.get("pending")),
            "earnings_24h_btc": round(earnings_24h, 8),
            "earnings_7d_btc": round(earnings_7d, 8),
            "total_rigs": len(rigs_output),
            "rigs_online": sum(1 for r in rigs_output if r["online"]),
            "active_rentals": sum(1 for r in rigs_output if r["rented"]),
            "utilization_7d_pct": round(total_util_7d / max(len(rigs_output), 1), 2),
            "current_mrr_btc_day_total": round(total_mrr_btc_day, 8),
            "fallback_btc_day_total": round(total_fallback_btc_day, 8),
            "mrr_minus_fallback_btc_day": round(total_mrr_btc_day - total_fallback_btc_day, 8),
        },
        "rigs": rigs_output,
    }

    earnings_daily: list[dict[str, Any]] = []
    for rig in rigs_output:
        rig_id = rig["id"]
        rig_name = rig["name"]
        for i in range(30):
            day = (beginning_of_day(now) - timedelta(days=29 - i)).date().isoformat()
            earnings_daily.append({
                "date": day,
                "rig_id": rig_id,
                "rig": rig_name,
                "btc": round(earnings_by_day_and_rig.get((day, rig_id), 0.0), 8),
            })

    (output_dir / "latest.json").write_text(json.dumps(latest, indent=2))
    (output_dir / "earnings_daily.json").write_text(json.dumps(earnings_daily, indent=2))
    (output_dir / "hashrate_history.json").write_text(json.dumps(hashrate_history, indent=2))
    print(f"Wrote JSON files to {output_dir}")


if __name__ == "__main__":
    main()
