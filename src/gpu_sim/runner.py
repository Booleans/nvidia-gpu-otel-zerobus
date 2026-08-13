"""Shared wiring for both entrypoints: parse args, build a MeterProvider whose OTLP exporter
points at a given endpoint (with optional headers), and run the fleet.

The only difference between the two entrypoints is the exporter target:
  - production -> a local Collector (plain OTLP, no auth here; the Collector adds it)
  - quickstart -> the Zerobus OTLP endpoint directly (Bearer token + table-name header)
Everything else -- the fleet, the instruments, the loop -- is identical.
"""

from __future__ import annotations

import argparse
import os

from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from .metrics import build_fleet, run_fleet


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hosts", type=int, default=4, help="number of simulated DGX nodes")
    p.add_argument("--gpus-per-host", type=int, default=8, help="GPUs per node (DGX H100 = 8)")
    p.add_argument("--interval", type=float, default=5.0, help="scrape interval seconds")
    p.add_argument("--iterations", type=int, default=6,
                   help="number of scrapes to emit, then exit (0 = run forever)")
    p.add_argument("--service-name", default="dcgm-gpu-simulator",
                   help="OTel service.name (lands in the table's service_name column)")


def build_meter_provider(endpoint: str, headers: dict | None, service_name: str,
                         interval_s: float) -> MeterProvider:
    """A MeterProvider with a single OTLP/gRPC exporter. `export_interval_millis` is set long
    so the run loop's explicit force_flush drives one export per scrape deterministically."""
    exporter = OTLPMetricExporter(endpoint=endpoint, headers=headers or None)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=3_600_000)
    resource = Resource.create({"service.name": service_name})
    return MeterProvider(metric_readers=[reader], resource=resource)


def run(endpoint: str, headers: dict | None, args: argparse.Namespace) -> None:
    provider = build_meter_provider(endpoint, headers, args.service_name, args.interval)
    meter = provider.get_meter("gpu_sim")
    fleet = build_fleet(args.hosts, args.gpus_per_host)
    n_gpu = len(fleet)
    iters = None if args.iterations == 0 else args.iterations
    print(f"simulating {args.hosts} hosts x {args.gpus_per_host} GPUs = {n_gpu} GPUs "
          f"-> {endpoint}  (every {args.interval}s, "
          f"{'forever' if iters is None else f'{iters} scrapes'})")
    try:
        run_fleet(provider, fleet, meter, args.interval, iters)
    finally:
        provider.force_flush(timeout_millis=10_000)
        provider.shutdown()
        print("done; flushed and shut down.")


def env(name: str, *fallbacks: str) -> str:
    for n in (name, *fallbacks):
        v = os.environ.get(n)
        if v:
            return v
    raise SystemExit(f"missing required env var {name}"
                     + (f" (or {', '.join(fallbacks)})" if fallbacks else ""))
