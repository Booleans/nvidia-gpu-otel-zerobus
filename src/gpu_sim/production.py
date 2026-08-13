"""Production-topology entrypoint: export to a LOCAL OpenTelemetry Collector.

This mirrors how a real DGX node runs -- the app (or dcgm-exporter) speaks plain OTLP to a
Collector on localhost, and the Collector holds the Databricks OAuth credentials and the
`x-databricks-zerobus-table-name` header, refreshing tokens on its own. Credentials never
live in the workload.

Run the collector first (see collector.yaml), then:
    uv run gpu-sim-production --collector-endpoint http://localhost:4317
"""

from __future__ import annotations

import argparse

from .runner import add_common_args, run


def main() -> None:
    p = argparse.ArgumentParser(description="GPU simulator -> local OTel Collector -> Zerobus")
    add_common_args(p)
    p.add_argument("--collector-endpoint", default="http://localhost:4317",
                   help="local Collector OTLP/gRPC endpoint")
    args = p.parse_args()
    # No auth headers here: the Collector attaches OAuth + the table-name header.
    run(args.collector_endpoint, headers=None, args=args)


if __name__ == "__main__":
    main()
