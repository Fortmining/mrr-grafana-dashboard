# MRR → Grafana Cloud dashboard pack

This pack builds a MiningRigRentals dashboard for Grafana Cloud using:

- **MiningRigRentals API v2**
- **GitHub Actions + GitHub Pages** as the free collector/hosting layer
- **Grafana Infinity** as the data source
- **Grafana HTTP API** to create the dashboard automatically

## Why this design

MiningRigRentals API v2 uses a custom HMAC auth scheme with `x-api-key`, `x-api-nonce`, and `x-api-sign`, where the signature is a SHA1 HMAC over `APIKEY + nonce + endpoint` signed with your API secret. Grafana Cloud’s HTTP API uses a **service account token** in the `Authorization: Bearer ...` header. Infinity is designed to query JSON/CSV/XML/GraphQL/HTML endpoints and can be configured from the Grafana UI. citeturn1view0turn1view2turn1view3

Because MRR uses custom per-request HMAC signing, the cleanest setup is to let a small script call MRR, normalize the JSON, publish it to GitHub Pages, and let Infinity read those JSON files. Infinity works out of the box once you create a data source instance, and Grafana supports creating dashboards through `POST /api/dashboards/db`. citeturn1view3turn1view1

## What this pack includes

- `mrr_collector.py` – pulls MRR rig, rental, balance, transaction, and graph data and writes normalized JSON
- `create_grafana_dashboard.py` – finds your Infinity datasource by name and creates/updates the dashboard through Grafana’s HTTP API
- `dashboard_template.json` – dashboard model used by the API script
- `.env.example` – variables to set
- `update_mrr_snapshot.yml` – GitHub Actions workflow example

## Files this creates in your repo

The collector writes:

- `docs/data/latest.json`
- `docs/data/earnings_daily.json`
- `docs/data/hashrate_history.json`

These are served by GitHub Pages and queried by Infinity.

## Required secrets / settings

Put these in GitHub Actions secrets or in your local environment:

- `MRR_API_KEY`
- `MRR_API_SECRET`
- `MRR_RIG_IDS`
- `GRAFANA_URL`
- `GRAFANA_TOKEN`
- `GRAFANA_INFINITY_NAME`

Optional, for fallback comparison:

- `RIG_372881_FALLBACK_BTC_DAY`
- `RIG_372932_FALLBACK_BTC_DAY`
- `RIG_372933_FALLBACK_BTC_DAY`

## Step by step

### 1) Create a GitHub repo

Create a new repo, for example:

`mrr-grafana-dashboard`

Copy the contents of this pack into that repo.

### 2) Put the collector and template files in the repo

Suggested structure:

```text
mrr-grafana-dashboard/
  .github/workflows/update_mrr_snapshot.yml
  scripts/mrr_collector.py
  scripts/create_grafana_dashboard.py
  dashboard_template.json
  docs/data/
```

### 3) Add GitHub Actions secrets

In GitHub:

**Settings → Secrets and variables → Actions → New repository secret**

Add:

- `MRR_API_KEY`
- `MRR_API_SECRET`
- `MRR_RIG_IDS` = `372881,372932,372933`
- `GRAFANA_URL` = your stack URL
- `GRAFANA_TOKEN` = your Grafana service account token
- `GRAFANA_INFINITY_NAME` = the exact name of your Infinity datasource instance in Grafana
- optional fallback secrets like `RIG_372881_FALLBACK_BTC_DAY`

### 4) Enable GitHub Pages

In GitHub:

**Settings → Pages**

Set:
- **Source** = `Deploy from a branch`
- **Branch** = `main`
- **Folder** = `/docs`

After the first workflow run, your JSON should be available at:

```text
https://<github-username>.github.io/<repo-name>/data/latest.json
https://<github-username>.github.io/<repo-name>/data/earnings_daily.json
https://<github-username>.github.io/<repo-name>/data/hashrate_history.json
```

### 5) Create an Infinity datasource in Grafana Cloud

In Grafana Cloud:

**Connections → Add new connection → Infinity**

Name it something like:

`mrr-pages`

You do not need auth for GitHub Pages if the repo is public. Infinity can query JSON endpoints directly after you create a datasource instance. citeturn1view3

### 6) Run the collector once

From GitHub:

**Actions → MRR Snapshot Update → Run workflow**

This will pull:

- `/rig/[id]` for rig state and prices
- `/rental` for active/history rentals
- `/account/balance` for wallet balances
- `/account/transactions` for BTC credits
- `/rig/[id]/graph` for historical hashrate

Those endpoints are documented by MRR. `GET /rig/[ID1];[ID2];...` returns rig status, prices, and hashrate windows. `GET /rental` lists rentals and supports owner/history filters. `GET /account/balance` returns balances. `GET /account/transactions` returns transaction history and allows filtering by rig and time. `GET /rig/[ID]/graph` returns historical bars/average/rejected/offline windows. citeturn4view1turn4view0turn4view2turn7view0

### 7) Create the dashboard automatically

Run:

```bash
python scripts/create_grafana_dashboard.py
```

The script:
- uses your Grafana Cloud service account token
- looks up the Infinity datasource by name
- substitutes the datasource UID into the template
- calls `POST /api/dashboards/db`

Grafana Cloud requires service account token auth for HTTP API calls, and dashboard creation uses the dashboard HTTP API. citeturn1view2turn1view1

### 8) Panels included on day one

The template builds:

- Wallet BTC
- 24h earnings BTC
- Active rentals
- Rig utilization %
- Rig status table
- Current hashrate by rig
- Utilization by rig
- Fallback comparison table
- Earnings by day
- Hashrate history by rig

### 9) Configure alerts

Recommended day-one alerts:

- rig offline
- rig not rented but hashrate = 0
- rented = true and hashrate below threshold
- wallet balance above your manual-withdraw threshold

The easiest path is:
1. open the panel
2. click **More → New alert rule**
3. set the condition on the reduced field

For alerting, Infinity works best with backend parsing modes such as JQ/JSONata when needed. citeturn0search11turn0search4

## Notes

- Grafana Cloud Free does **not** run your collector script for you. It hosts Grafana. The free compute layer in this design is GitHub Actions + GitHub Pages.
- MRR API v2 is marked beta in their docs. citeturn1view0
- The MRR docs state `PUT /account/balance` withdrawal is disabled, so this dashboard is read-only for wallet monitoring. citeturn4view2

## Security

Because your MRR API secret and Grafana token were pasted into chat, rotate both after you get the first successful test. Do **not** commit real secrets into the repo.
