"""A stand-in for NVIDIA dcgm-exporter.

The real dcgm-exporter runs on each GPU node and serves DCGM fields in Prometheus text format
on ``:9400/metrics``. This module serves the *same wire format* with the same metric names
(``DCGM_FI_DEV_*``) and the same identifying labels (``gpu``, ``UUID``, ``modelName``,
``Hostname``, ``DCGM_FI_DRIVER_VERSION``), driven by the shared fleet simulator in metrics.py.

The point of this variant: point an OpenTelemetry Collector's ``prometheus`` receiver at this
endpoint and you have the exact topology NVIDIA runs -- dcgm-exporter -> Collector -> backend
-- with only the metric *source* faked. To use a real dcgm-exporter instead, drop this and
point the Collector at the real ``:9400/metrics`` (see README).

Run:  uv run gpu-sim-dcgm-exporter --hosts 4 --gpus-per-host 8 --port 9400
Then: curl localhost:9400/metrics
"""

from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .metrics import GPU_MODEL, MEM_TOTAL_BYTES, Gpu, build_fleet

DRIVER_VERSION = "550.90.07"

# (DCGM field, Prometheus type, help, value function). Names/types match dcgm-exporter's
# default-counters.csv so the scraped series look exactly like production.
FIELDS = [
    ("DCGM_FI_DEV_GPU_UTIL",    "gauge", "GPU utilization (in %).",            lambda g: g.util),
    ("DCGM_FI_DEV_GPU_TEMP",    "gauge", "GPU temperature (in C).",            lambda g: g.temp_c),
    ("DCGM_FI_DEV_POWER_USAGE", "gauge", "Power draw (in W).",                 lambda g: g.power_w),
    ("DCGM_FI_DEV_SM_CLOCK",    "gauge", "SM clock frequency (in MHz).",       lambda g: g.sm_clock_mhz),
    ("DCGM_FI_DEV_FB_USED",     "gauge", "Framebuffer memory used (in MiB).",  lambda g: g.mem_used_frac * MEM_TOTAL_BYTES / 1024**2),
    ("DCGM_FI_DEV_FB_FREE",     "gauge", "Framebuffer memory free (in MiB).",  lambda g: (1 - g.mem_used_frac) * MEM_TOTAL_BYTES / 1024**2),
    # Real dcgm-exporter exposes cumulative ECC as a counter; keep it here so the Collector
    # scrapes a counter too (lands in the Delta `sum` column).
    ("DCGM_FI_DEV_ECC_DBE_AGG_TOTAL", "counter", "Total uncorrectable ECC errors.", lambda g: float(g.ecc_errors)),
]


def _labels(g: Gpu) -> str:
    # dcgm-exporter's standard label set. `gpu` is the local index; UUID/modelName/Hostname
    # identify the device and node. These become OTel attributes after the Collector scrapes.
    return (
        f'gpu="{g.gpu_id}",'
        f'UUID="{g.uuid}",'
        f'modelName="{GPU_MODEL}",'
        f'Hostname="{g.host}",'
        f'DCGM_FI_DRIVER_VERSION="{DRIVER_VERSION}",'
        f'cloud_provider="{g.cloud}"'
    )


def render(fleet: list[Gpu]) -> str:
    lines: list[str] = []
    for field, mtype, help_text, fn in FIELDS:
        lines.append(f"# HELP {field} {help_text}")
        lines.append(f"# TYPE {field} {mtype}")
        for g in fleet:
            lines.append(f"{field}{{{_labels(g)}}} {fn(g):.6g}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    fleet: list[Gpu] = []
    last_tick: float = 0.0

    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        # Advance the sim at most ~once/sec so repeated scrapes see fresh-but-stable values.
        now = time.time()
        if now - Handler.last_tick > 1.0:
            for g in Handler.fleet:
                g.tick()
            Handler.last_tick = now
        body = render(Handler.fleet).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # quiet
        pass


def main() -> None:
    p = argparse.ArgumentParser(description="Simulated NVIDIA dcgm-exporter (Prometheus /metrics)")
    p.add_argument("--hosts", type=int, default=4)
    p.add_argument("--gpus-per-host", type=int, default=8)
    p.add_argument("--port", type=int, default=9400, help="dcgm-exporter's standard port")
    args = p.parse_args()

    Handler.fleet = build_fleet(args.hosts, args.gpus_per_host)
    n = len(Handler.fleet)
    print(f"simulated dcgm-exporter: {args.hosts} hosts x {args.gpus_per_host} GPUs = {n} GPUs")
    print(f"serving DCGM_FI_* metrics at http://localhost:{args.port}/metrics  (Ctrl-C to stop)")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
