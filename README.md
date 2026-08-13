# nvidia-gpu-otel-zerobus

A runnable example that simulates **NVIDIA DGX GPU-node telemetry** and streams it into a
**Databricks Delta table** through **Zerobus OpenTelemetry (OTLP) ingest** — the same path a
real DGX fleet would use, with an OpenTelemetry Collector on the node forwarding metrics to
Databricks.

It's built for the DGX Cloud pattern: GPU nodes running across **AWS, GCP, Azure, and
Oracle**, each emitting DCGM-style metrics (utilization, memory, temperature, power, clocks,
ECC errors) that land in one governed Delta table for fleet-wide analytics.

> **Proven end-to-end.** A live run of 4 simulated DGX H100 nodes (32 GPUs, one per cloud)
> streamed through an OpenTelemetry Collector into Zerobus and landed **6 metrics** in Delta —
> 5 gauges plus a monotonic ECC counter — with every per-GPU attribute (`gpu.id`,
> `cloud.provider`, `host.name`, …) queryable as native VARIANT.

---

## How real DGX telemetry maps to this example

On a real DGX node the chain is **DCGM → dcgm-exporter → OpenTelemetry Collector → backend**.
This example keeps that topology and swaps only the metric source (simulated) and the backend
(Databricks Zerobus):

```
   simulated GPU node                         Databricks
 ┌───────────────────────────┐   OTLP/gRPC   ┌──────────────────────────────┐
 │ gpu_sim (OTel SDK)          │  (localhost)  │  OTel Collector (on-node)     │
 │  DCGM-style metrics ────────┼──────────────►│   receiver: otlp              │
 │  per GPU x N GPUs           │               │   processor: batch            │
 └───────────────────────────┘               │   exporter: otlp ──┐          │
                                              │   auth: oauth2client│          │
                                              └─────────────────────┼─────────┘
                                                                     │ OTLP + OAuth
                                                                     │ + table header
                                                                     ▼
                                              ┌──────────────────────────────┐
                                              │  Zerobus OTLP endpoint         │
                                              │   → <catalog>.<schema>.        │
                                              │       gpu_otel_metrics (Delta) │
                                              └──────────────────────────────┘
```

In production you'd point the Collector's `prometheus` receiver at a real dcgm-exporter
instead of running `gpu_sim`; everything downstream of the Collector is identical.

---

## Layout

```
src/gpu_sim/
  metrics.py        DCGM-style metric definitions + fleet simulation (shared by all entrypoints)
  runner.py         MeterProvider / OTLP exporter wiring + CLI
  production.py     entrypoint: OTel SDK -> local Collector  (realistic node topology)
  quickstart.py     entrypoint: OTel SDK -> Zerobus direct   (fewest moving parts)
  dcgm_exporter.py  entrypoint: simulated dcgm-exporter serving DCGM_FI_* on :9400/metrics
collector.yaml      Collector config: otlp receiver (SDK path)         -> Zerobus
collector-dcgm.yaml Collector config: prometheus receiver (scrape dcgm) -> Zerobus
.env.example        config template (copy to .env)
pyproject.toml      Python deps (uv)
```

Three ways to feed the same pipeline, in increasing fidelity to a real DGX node:

| Entrypoint | Source | Path | Closest to |
|---|---|---|---|
| `gpu-sim-quickstart` | OTel SDK | → Zerobus direct | a first smoke test |
| `gpu-sim-production` | OTel SDK | → Collector → Zerobus | an app instrumented with OTel |
| `gpu-sim-dcgm-exporter` | **dcgm-exporter** | → Collector scrape → Zerobus | **a real DGX node** |

---

## Prerequisites

- **Python 3.9+** and [uv](https://docs.astral.sh/uv/), with access to PyPI.
- **`otelcol-contrib`** (the *contrib* distribution) for the production path — the
  `oauth2client` auth extension is not in the core collector. `brew install
  opentelemetry-collector-contrib`, or download from the
  [releases](https://github.com/open-telemetry/opentelemetry-collector-releases).
- A **Databricks service principal** (`client_id` + OAuth secret) with the full Unity Catalog
  grant chain on the target table: **`USE CATALOG`** on the catalog, **`USE SCHEMA`** on the
  schema, **`SELECT` + `MODIFY`** on the table (see [Authentication](#authentication)).
- The **target Delta table**, pre-created with the OpenTelemetry metrics schema — Zerobus does
  not create or alter tables (see [The table](#the-table)).

---

## Quick start (production path — recommended)

```bash
# 1. install deps
uv sync

# 2. configure
cp .env.example .env      # then fill in workspace, SP, and table values

# 3. run the on-node Collector (holds the OAuth creds + table header)
set -a; . ./.env; set +a
otelcol-contrib --config collector.yaml     # leave running in one terminal

# 4. in another terminal, simulate a GPU fleet exporting to the local Collector
uv run gpu-sim-production --hosts 4 --gpus-per-host 8 --iterations 6 --interval 2
```

`--iterations 0` runs forever; drop `--hosts`/`--gpus-per-host` to scale the simulated fleet.

### Simplest path (no Collector)

The app can export straight to Zerobus, carrying the token and table header itself. Fewer
moving parts, but credentials live in the workload — prefer the Collector on real nodes.

```bash
# .env must have ZEROBUS_OTLP_ENDPOINT, DATABRICKS_TOKEN (an OAuth access token), ZEROBUS_TABLE
uv run gpu-sim-quickstart --hosts 4 --gpus-per-host 8 --iterations 6
```

### dcgm-exporter path (closest to a real DGX node)

This is the production topology: **dcgm-exporter** serves DCGM fields in Prometheus format on
`:9400/metrics`, and the Collector's `prometheus` receiver scrapes them. Here we fake the
exporter; on a real node you'd run the actual
[dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) and change only the scrape target.

```bash
# terminal 1 — the (simulated) dcgm-exporter, real DCGM_FI_* names + labels
uv run gpu-sim-dcgm-exporter --hosts 4 --gpus-per-host 8 --port 9400
#   sanity check:  curl localhost:9400/metrics

# terminal 2 — the Collector scrapes :9400 and forwards to Zerobus
set -a; . ./.env; set +a
otelcol-contrib --config collector-dcgm.yaml
```

Metrics land under their native DCGM names (`DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_GPU_TEMP`,
`DCGM_FI_DEV_ECC_DBE_AGG_TOTAL`, …) with dcgm-exporter's labels (`gpu`, `UUID`, `modelName`,
`Hostname`) preserved as VARIANT attributes.

**To use a real dcgm-exporter:** skip terminal 1 and point `collector-dcgm.yaml`'s
`scrape_configs.targets` at your dcgm-exporter endpoint(s) — one per node, or a
service-discovery list. Nothing else changes.

---

## Verify

```sql
-- rows landed, one row per metric per GPU per scrape
SELECT name, metric_type, count(*)
FROM <catalog>.<schema>.gpu_otel_metrics
GROUP BY name, metric_type ORDER BY name;

-- per-cloud fleet view: attributes are queryable VARIANT (note the bracket path for dotted keys)
SELECT
  variant_get(gauge.attributes, '$["cloud.provider"]', 'string') AS cloud,
  count(distinct variant_get(gauge.attributes, '$["gpu.id"]', 'int')) AS gpus,
  round(avg(gauge.value), 1) AS avg_temp_c
FROM <catalog>.<schema>.gpu_otel_metrics
WHERE name = 'gpu.temperature'
GROUP BY cloud ORDER BY cloud;

-- the ECC monotonic counter lands in the `sum` column
SELECT variant_get(sum.attributes, '$["gpu.id"]', 'int') AS gpu,
       max(sum.value) AS cumulative_ecc, any_value(sum.is_monotonic) AS monotonic
FROM <catalog>.<schema>.gpu_otel_metrics
WHERE name = 'gpu.ecc.errors'
GROUP BY gpu ORDER BY cumulative_ecc DESC;
```

---

## The table

Zerobus ingests OpenTelemetry metrics into a **predefined schema** — you create the table, the
OTLP receiver populates it. Gauges land in the `gauge` struct, monotonic counters in `sum`,
and every OTel attribute set (`gauge.attributes`, `resource.attributes`, …) is a **VARIANT**
column, so arbitrary per-GPU labels are preserved and queryable without schema changes.

The full DDL is in [`table.sql`](table.sql). Key points: it's a managed Delta table,
`CLUSTER BY (time, service_name)`, with `'otel.schemaVersion' = 'v2'` and variant shredding
enabled.

---

## Authentication

Zerobus OTLP authenticates with a **service principal** via OAuth. The subtlety that trips
people up: the access token must carry a Unity Catalog **`authorization_details`** claim
enumerating the privilege chain to the table, or ingest fails with *"Missing authorization
details in access token claims"* even when the SP genuinely holds the grants.

`collector.yaml` handles this through the `oauth2client` extension's `endpoint_params`
(`resource` + `authorization_details`). You still need the grants themselves:

```sql
GRANT USE CATALOG ON CATALOG <catalog>                     TO `<sp-application-id>`;
GRANT USE SCHEMA  ON SCHEMA  <catalog>.<schema>            TO `<sp-application-id>`;
GRANT SELECT, MODIFY ON TABLE <catalog>.<schema>.<table>  TO `<sp-application-id>`;
```

Mint the SP OAuth secret (used by the Collector) with:

```bash
databricks service-principal-secrets-proxy create <sp-numeric-id> \
  --profile <profile> --lifetime 86400s
```

Endpoint format (AWS): `https://<workspace-id>.zerobus.<region>.cloud.databricks.com:443`.

---

## Scale note

This example runs a handful of GPUs, but the pattern is built to scale, and the key fact is
that **batching happens at the Collector**: one export request carries a whole scrape interval
across all of a node's GPUs (hundreds of data points), so request rate is driven by *number of
collectors × scrape cadence*, not by GPU or metric count.

- Zerobus OTLP quota: **10,000 requests/sec** (adjustable). With one agent Collector per node
  emitting ~1 request per scrape, a 15–60s cadence keeps a very large fleet well within quota.
- For very large fleets, add a **gateway Collector tier** (per-node agents → regional gateways
  that re-batch → Zerobus) to cut request count further.
- The dimension to plan around isn't the protocol — it's **raw row volume**: `GPUs × metrics ÷
  cadence` data points/sec landing in Delta. At fleet scale, choose metrics deliberately and
  consider longer cadences or aggregation rather than landing every raw point.

### Numeric precision (real for GPU counters)

Zerobus stores integer metric values as `DOUBLE`, which **loses precision above 2^53**, and
unsigned OTLP fields become signed (values above `i64::MAX` wrap negative). Large cumulative
GPU counters (e.g. total ECC errors, bytes moved, energy) can reach these ranges — keep it in
mind for production; it's harmless at demo scale.

---

## Why OTLP, not Zerobus Arrow Flight?

Zerobus has a separate **Arrow Flight** ingestion path (declare your own schema, push
`RecordBatch`es). It's excellent for high-volume custom telemetry — but it's the wrong tool
here, for three reasons:

1. **It doesn't connect to the OTel ecosystem.** An OpenTelemetry Collector exports OTLP, not
   Arrow Flight — there's no wire path from dcgm-exporter/collector into the Arrow Flight API.
2. **You'd lose the standard tooling.** NVIDIA's stack is OTel-native (DCGM → dcgm-exporter →
   Collector). OTLP plugs straight in; Arrow Flight would mean hand-building a custom producer
   and schema and reinventing what OpenTelemetry already gives you.
3. **Batching efficiency is already there.** OTLP isn't per-record — the Collector's `batch`
   processor coalesces a whole scrape into one request, so the efficiency Arrow Flight is
   prized for is already present for this workload.

Arrow Flight earns its place when you're pushing a firehose of your *own* schema and want
columnar wire efficiency and explicit backpressure/ACKs. For standard GPU metrics from a
collector, OTLP is the right door.

---

## Notes

- **Zerobus OpenTelemetry ingestion is evolving** — check the
  [Databricks OpenTelemetry ingest docs](https://docs.databricks.com/ingestion/opentelemetry/)
  for current status, regions, and limits.
- **Never commit secrets** — `.gitignore` excludes `.env`.
- The simulator's metric names mirror DCGM fields (e.g. `gpu.temperature` ↔
  `DCGM_FI_DEV_GPU_TEMP`); see `metrics.py` for the mapping.
