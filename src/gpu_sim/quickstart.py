"""Quickstart entrypoint: export OTLP straight to the Zerobus endpoint, no Collector.

Simplest way to see data land -- the app itself carries the OAuth Bearer token and the
target-table header. Good for a first run; for a realistic node deployment prefer the
Collector path (production.py), which keeps credentials out of the workload and refreshes
tokens automatically.

Required env (see .env.example):
    ZEROBUS_OTLP_ENDPOINT   e.g. https://<workspace-id>.zerobus.<region>.cloud.databricks.com:443
    DATABRICKS_TOKEN        OAuth access token for the service principal (see README to mint)
    ZEROBUS_TABLE           <catalog>.<schema>.gpu_otel_metrics

    uv run gpu-sim-quickstart
"""

from __future__ import annotations

import argparse

from .runner import add_common_args, env, run


def main() -> None:
    p = argparse.ArgumentParser(description="GPU simulator -> Zerobus OTLP endpoint (direct)")
    add_common_args(p)
    args = p.parse_args()

    endpoint = env("ZEROBUS_OTLP_ENDPOINT")
    token = env("DATABRICKS_TOKEN")
    table = env("ZEROBUS_TABLE")
    # OTLP/gRPC metadata: the SP Bearer token and the Zerobus target-table header (one table
    # per exporter). These are exactly the headers the Collector would otherwise add.
    headers = {
        "authorization": f"Bearer {token}",
        "x-databricks-zerobus-table-name": table,
    }
    run(endpoint, headers=headers, args=args)


if __name__ == "__main__":
    main()
