"""One interruptible worker and request limiter for the entire application."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from queue import Empty, Queue
import threading
import time

from .config import Config
from .modbus import ModbusException, PanelClient
from .state import read_plan


LOG = logging.getLogger(__name__)


class RequestLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_request: float | None = None

    def wait(self, stop, interval: float) -> bool:
        delay = 0 if self.last_request is None else max(0, self.last_request + max(1.0, interval) - self.clock())
        if stop.wait(delay):
            return False
        self.last_request = self.clock()
        return True


@dataclass
class SimulationControls:
    outage: threading.Event = field(default_factory=threading.Event)
    paused: threading.Event = field(default_factory=threading.Event)
    step: int = 0
    fault_module: int = 0


class SimulationClient:
    def __init__(self, config: Config, controls: SimulationControls):
        self.controls = controls
        self.summaries = {ref - 1: mid for mid, ref in config.summary_references().items()}

    def connect(self):
        if self.controls.outage.is_set():
            raise OSError("Simulated connection outage.")

    def close(self):
        pass

    def read_holding_registers(self, address, count):
        self.connect()
        phase = int(time.monotonic() / 3) + self.controls.step
        values = []
        for ref in range(address, address + count):
            if ref in self.summaries:
                values.append(4 if self.controls.fault_module == self.summaries[ref] else 0)
            else:
                offset = ref - 0x5000
                values.append(sum(1 << n for n in range(4) if (phase + offset + n) % 3 == 0))
        return values


class MonitorEngine:
    def __init__(self, events: Queue, transport_factory=None):
        self.events = events
        self.transport_factory = transport_factory
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.connected = threading.Event()
        self.reset_pending = threading.Event()
        self.commands = Queue(maxsize=1)
        self.limiter = RequestLimiter()
        self.client = None
        self.simulation = SimulationControls()
        self.session = 0
        self.simulated = False
        self.config: Config | None = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def emit(self, kind, **payload):
        self.events.put((self.session, kind, payload))

    def start(self, config: Config, simulated=False):
        if self.running:
            raise RuntimeError("Wait for the current connection to finish closing.")
        self.session += 1
        self.config, self.simulated = config, simulated
        self.stop_event = threading.Event()
        self.connected.clear()
        self.reset_pending.clear()
        self.commands = Queue(maxsize=1)
        self.simulation = SimulationControls()
        self.thread = threading.Thread(target=self._run, name="kide-modbus", daemon=True)
        self.thread.start()
        return self.session

    def stop(self):
        self.stop_event.set()
        self.connected.clear()

    def request_reset(self):
        if not self.running or not self.connected.is_set() or self.simulated or not self.config.reset.enabled:
            raise RuntimeError("Panel reset is available only on a connected live session.")
        if self.reset_pending.is_set():
            raise RuntimeError("A reset request is already pending.")
        self.reset_pending.set()
        self.commands.put_nowait(self.config.reset.target_panel_id)
        LOG.info("Reset queued for panel target %s", self.config.reset.target_panel_id)

    def _cancel_commands(self):
        try:
            self.commands.get_nowait()
        except Empty:
            pass
        else:
            self.emit("reset", outcome="cancelled", message="Reset cancelled before transmission; it will not be replayed.")
        self.reset_pending.clear()

    def _make_client(self):
        if self.transport_factory:
            return self.transport_factory(self.config)
        if self.simulated:
            return SimulationClient(self.config, self.simulation)
        c = self.config.connection
        return PanelClient(c.host, c.port, c.unit_id, c.timeout_seconds)

    def _execute_reset(self, target):
        if not self.limiter.wait(self.stop_event, self.config.polling.request_interval_seconds):
            self.reset_pending.clear()
            self.emit("reset", outcome="cancelled", message="Reset cancelled before transmission.")
            return False
        LOG.info("Sending Execute Reset once to target %s", target)
        reconnect = True
        try:
            self.client.execute_reset(target)
        except ModbusException as exc:
            # Exception 05 means processing, not a definitive rejection.
            outcome = "unknown" if exc.code == 5 else "rejected"
            reconnect = outcome == "unknown"
            self.emit("reset", outcome=outcome, message=str(exc) + "; reset will not be retried.")
            LOG.warning("Reset %s: %s", outcome, exc)
        except OSError as exc:
            self.emit("reset", outcome="unknown", message=f"Reset outcome unknown: {exc}. It will not be retried.")
            LOG.warning("Reset outcome unknown: %s", exc)
        else:
            self.emit("reset", outcome="acknowledged", message="Execute Reset acknowledged. Waiting for fresh panel data.")
            LOG.info("Execute Reset acknowledged")
        finally:
            self.reset_pending.clear()
        return reconnect

    def _run(self):
        plan = read_plan(self.config)
        polling = self.config.polling
        retry = polling.reconnect_initial_seconds
        try:
            while not self.stop_event.is_set():
                self.emit("status", text="Connecting", connected=False)
                self.client = self._make_client()
                try:
                    self.client.connect()
                    if self.stop_event.is_set():
                        break
                    self.connected.set()
                    self.emit("status", text="Simulation running" if self.simulated else "Connected", connected=True)
                    LOG.info("%s connected", "Simulation" if self.simulated else "Panel")
                    index = 0
                    failures = {}
                    while not self.stop_event.is_set():
                        try:
                            target = self.commands.get_nowait()
                        except Empty:
                            target = None
                        if target is not None:
                            if self._execute_reset(target):
                                break
                        if self.simulated and self.simulation.paused.is_set():
                            self.stop_event.wait(0.1)
                            continue
                        if not plan:
                            self.stop_event.wait(0.1)
                            continue
                        if not self.limiter.wait(self.stop_event, polling.request_interval_seconds):
                            break
                        batch = plan[index]
                        try:
                            values = self.client.read_holding_registers(batch.pdu_address, batch.count)
                        except ModbusException as exc:
                            failures[batch.reference] = str(exc)
                            self.emit("read_error", reference=batch.reference, count=batch.count, message=str(exc))
                            LOG.warning("Read reference 0x%04X: %s", batch.reference, exc)
                        else:
                            if len(values) != batch.count or any(type(v) is not int or not 0 <= v <= 65535 for v in values):
                                raise OSError("Malformed register data received.")
                            retry = polling.reconnect_initial_seconds
                            failures.pop(batch.reference, None)
                            self.emit("sample", reference=batch.reference, values=values,
                                      monotonic=time.monotonic(), wall_time=time.time())
                        self.emit("error", message="; ".join(failures.values()))
                        index = (index + 1) % len(plan)
                except OSError as exc:
                    if not self.stop_event.is_set():
                        LOG.warning("Connection interrupted: %s", exc)
                        self.emit("error", message=str(exc))
                finally:
                    self.connected.clear()
                    self.client.close()
                    self._cancel_commands()
                if not self.stop_event.is_set():
                    self.emit("status", text=f"Reconnecting in {retry:g}s", connected=False)
                    if self.stop_event.wait(retry):
                        break
                    retry = min(retry * 2, polling.reconnect_max_seconds)
        except Exception as exc:
            LOG.exception("Monitoring worker stopped unexpectedly")
            self.emit("error", message=f"Monitoring stopped: {exc}")
        finally:
            self.connected.clear()
            if self.client:
                self.client.close()
            self._cancel_commands()
            self.emit("status", text="Disconnected", connected=False)
