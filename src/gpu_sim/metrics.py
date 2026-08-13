"""Simulated DCGM-style GPU telemetry.

Models a small fleet of NVIDIA DGX GPU nodes and emits the metrics NVIDIA's Data Center GPU
Manager (DCGM) actually exposes -- utilization, memory, temperature, power, clocks, and
cumulative ECC error counts -- through the OpenTelemetry metrics API. Both entrypoints
(direct export and via-collector) build a MeterProvider and call `run_fleet` from here, so
the metric definitions live in exactly one place.

Instrument choice mirrors DCGM semantics:
  - gauges (ObservableGauge) for instantaneous readings: utilization, memory, temp, power, clock
  - a monotonic Counter for ECC errors, which only ever accumulates

Why a callback-based ObservableGauge rather than set(): OTel collects observable instruments
on the export interval, so one registered callback per metric naturally produces one data
point per GPU per scrape -- exactly how dcgm-exporter behaves.
"""

from __future__ import annotations

import itertools
import random
import time
from dataclasses import dataclass

from opentelemetry.metrics import CallbackOptions, Observation


# The four clouds NVIDIA runs DGX Cloud on. Each simulated host is pinned to one.
CLOUDS = ["aws", "gcp", "azure", "oracle"]
GPU_MODEL = "NVIDIA H100 80GB HBM3"
MEM_TOTAL_BYTES = 80 * 1024**3  # 80 GB HBM3


@dataclass
class Gpu:
    """One simulated GPU with slowly-drifting state, so successive scrapes look realistic
    (values wander rather than jumping randomly each interval)."""

    gpu_id: int
    uuid: str
    host: str
    cloud: str
    util: float = 40.0          # %
    mem_used_frac: float = 0.35
    temp_c: float = 45.0
    power_w: float = 300.0
    sm_clock_mhz: float = 1400.0
    ecc_errors: int = 0         # monotonic

    def tick(self) -> None:
        """Advance one scrape interval with bounded random-walk dynamics."""
        # Utilization drives the correlated signals (busier GPU -> hotter, more power).
        self.util = _clamp(self.util + random.uniform(-15, 15), 0, 100)
        load = self.util / 100.0
        self.mem_used_frac = _clamp(self.mem_used_frac + random.uniform(-0.05, 0.05), 0.05, 0.98)
        self.temp_c = _clamp(30 + load * 45 + random.uniform(-3, 3), 30, 90)
        self.power_w = _clamp(100 + load * 600 + random.uniform(-20, 20), 100, 700)
        self.sm_clock_mhz = _clamp(1200 + load * 785 + random.uniform(-50, 50), 210, 1980)
        # ECC errors accumulate rarely and never decrease. Real fleets see them infrequently,
        # but we want the monotonic-counter path exercised in a short demo run, so a subset of
        # GPUs (deterministically, by id) tick up most intervals -- enough to populate the
        # `sum` column and show cumulative counter handling without being unrealistic.
        if self.gpu_id % 4 == 0 and random.random() < 0.7:
            self.ecc_errors += random.randint(1, 3)

    def resource_attrs(self) -> dict:
        """Per-GPU identity that rides along on every data point as OTel attributes.
        These land in the VARIANT `attributes` map and are queryable via variant_get."""
        return {
            "gpu.id": self.gpu_id,
            "gpu.uuid": self.uuid,
            "gpu.model": GPU_MODEL,
            "host.name": self.host,
            "cloud.provider": self.cloud,
        }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def build_fleet(num_hosts: int, gpus_per_host: int) -> list[Gpu]:
    """A fleet of `num_hosts` DGX nodes, each with `gpus_per_host` GPUs, round-robin across
    the four clouds so a demo query can group by cloud.provider."""
    fleet: list[Gpu] = []
    for h in range(num_hosts):
        cloud = CLOUDS[h % len(CLOUDS)]
        host = f"dgx-{cloud}-{h:03d}"
        for g in range(gpus_per_host):
            fleet.append(
                Gpu(
                    gpu_id=g,
                    uuid=f"GPU-{h:03d}-{g}-{random.randint(0, 0xFFFFFF):06x}",
                    host=host,
                    cloud=cloud,
                )
            )
    return fleet


def register_instruments(meter, fleet: list[Gpu]) -> None:
    """Register one observable gauge per DCGM reading plus a monotonic ECC counter. Each
    gauge callback yields one Observation per GPU, tagged with that GPU's attributes."""

    def _obs(read):
        def cb(_options: CallbackOptions):
            return [Observation(read(g), g.resource_attrs()) for g in fleet]
        return cb

    meter.create_observable_gauge(
        "gpu.utilization", callbacks=[_obs(lambda g: g.util)],
        unit="%", description="GPU compute utilization (DCGM_FI_DEV_GPU_UTIL)",
    )
    meter.create_observable_gauge(
        "gpu.memory.used", callbacks=[_obs(lambda g: g.mem_used_frac * MEM_TOTAL_BYTES)],
        unit="By", description="Frame-buffer memory used (DCGM_FI_DEV_FB_USED)",
    )
    meter.create_observable_gauge(
        "gpu.temperature", callbacks=[_obs(lambda g: g.temp_c)],
        unit="Cel", description="GPU core temperature (DCGM_FI_DEV_GPU_TEMP)",
    )
    meter.create_observable_gauge(
        "gpu.power.usage", callbacks=[_obs(lambda g: g.power_w)],
        unit="W", description="Board power draw (DCGM_FI_DEV_POWER_USAGE)",
    )
    meter.create_observable_gauge(
        "gpu.sm.clock", callbacks=[_obs(lambda g: g.sm_clock_mhz)],
        unit="MHz", description="Streaming-multiprocessor clock (DCGM_FI_DEV_SM_CLOCK)",
    )
    # ECC errors: monotonic counter. NOTE: a real cumulative ECC count can grow very large;
    # Zerobus stores integer metric values as DOUBLE, which loses precision above 2^53. Fine
    # for a demo, but flag it for production (see README "Numeric precision").
    ecc = meter.create_counter(
        "gpu.ecc.errors", unit="1",
        description="Cumulative uncorrectable ECC errors (DCGM_FI_DEV_ECC_DBE_AGG_TOTAL)",
    )
    # Prime observed-counter state; the run loop increments it each tick.
    fleet_ecc_seen = {id(g): 0 for g in fleet}

    def advance_ecc():
        for g in fleet:
            delta = g.ecc_errors - fleet_ecc_seen[id(g)]
            if delta:
                ecc.add(delta, g.resource_attrs())
                fleet_ecc_seen[id(g)] = g.ecc_errors

    register_instruments._advance_ecc = advance_ecc  # type: ignore[attr-defined]


def run_fleet(meter_provider, fleet: list[Gpu], meter, interval_s: float, iterations: int | None) -> None:
    """Drive the simulation: each interval, advance every GPU's state and force a metric
    export. Runs `iterations` scrapes (None = forever). The MeterProvider's PeriodicExporting
    MetricReader is configured by the caller; here we advance state and flush on each tick."""
    register_instruments(meter, fleet)
    advance_ecc = register_instruments._advance_ecc  # type: ignore[attr-defined]

    counter = itertools.count()
    while iterations is None or next(counter) < iterations:
        for g in fleet:
            g.tick()
        advance_ecc()
        # Force collection+export now so each loop == one scrape, rather than relying solely
        # on the periodic reader's own timer.
        meter_provider.force_flush(timeout_millis=10_000)
        time.sleep(interval_s)
