"""Pure read planning, decoding and freshness logic, shared by both windows."""

from dataclasses import dataclass
import time

from .config import Config, FAULT_LABELS


@dataclass(frozen=True)
class ReadBatch:
    reference: int
    count: int

    @property
    def pdu_address(self):
        return self.reference - 1


def read_plan(config: Config) -> tuple[ReadBatch, ...]:
    references = set(config.summary_references().values())
    references.update(ch.mapping.register_reference for _, ch in config.channels() if ch.mapping)
    references.update(rule.register_reference for _, ch in config.channels() if ch.mapping
                      for rule in ch.mapping.fault_rules)
    batches = []
    for ref in sorted(references):
        if batches and ref == batches[-1].reference + batches[-1].count and batches[-1].count < 4:
            previous = batches[-1]
            batches[-1] = ReadBatch(previous.reference, previous.count + 1)
        else:
            batches.append(ReadBatch(ref, 1))
    return tuple(batches)


@dataclass
class Reading:
    value: int | None = None
    monotonic: float | None = None
    wall_time: float | None = None
    error: str = ""


@dataclass(frozen=True)
class ChannelView:
    state: str
    detail: str
    last_value: bool | None = None
    last_update: float | None = None
    source_kind: str = ""


class MonitorState:
    def __init__(self, config: Config):
        self.config = config
        self.plan = read_plan(config)
        self.scan_seconds = max(1, len(self.plan)) * config.polling.request_interval_seconds
        self.stale_seconds = max(config.polling.stale_min_seconds,
                                 self.scan_seconds * config.polling.stale_scan_multiplier)
        self.readings: dict[int, Reading] = {}
        self.connected = False
        self.last_update: float | None = None
        self.channels = dict(config.channels())
        self.summaries = config.summary_references()

    def apply_read(self, reference: int, values: list[int], monotonic=None, wall_time=None):
        monotonic = time.monotonic() if monotonic is None else monotonic
        wall_time = time.time() if wall_time is None else wall_time
        for offset, value in enumerate(values):
            self.readings[reference + offset] = Reading(value, monotonic, wall_time)
        self.last_update = wall_time

    def fail_read(self, reference: int, count: int, error: str):
        for ref in range(reference, reference + count):
            self.readings.setdefault(ref, Reading()).error = error

    def disconnect(self):
        self.connected = False
        for reading in self.readings.values():
            reading.error = "Connection interrupted; waiting for a fresh reading."

    def _problem(self, reading: Reading | None, now: float):
        if reading is None:
            return "Unknown", "Waiting for the first reading."
        if not self.connected or reading.error:
            return "Unavailable", reading.error or "Not connected."
        if reading.monotonic is None:
            return "Unknown", "Waiting for the first reading."
        if now - reading.monotonic >= self.stale_seconds:
            return "Stale", f"No fresh reading for {now - reading.monotonic:.0f} seconds."
        return None

    def module_conditions(self, module_id: int, now=None) -> tuple[str, ...]:
        now = time.monotonic() if now is None else now
        ref = self.summaries.get(module_id)
        if ref is None:
            return ()
        reading = self.readings.get(ref)
        problem = self._problem(reading, now)
        if problem:
            return (f"Status {problem[0].lower()}",)
        return tuple(label for bit, label in enumerate(("Prealarm", "Alarm", "Fault", "Test", "Disabled"))
                     if reading.value & (1 << bit))

    def _diagnostic_state(self, mapping, now):
        groups = {}
        for rule in mapping.fault_rules:
            reading = self.readings.get(rule.register_reference)
            problem = self._problem(reading, now)
            if problem:
                return "Unavailable", f"Circuit diagnosis is unavailable: {problem[1]}"
            groups.setdefault((rule.register_reference, rule.mask, rule.normal_value), []).append(rule)
        matched = []
        for (ref, mask, normal), rules in groups.items():
            value = self.readings[ref].value & mask
            selected = [r for r in rules if r.value == value]
            if not selected and value != normal:
                return "Fault", f"Unrecognized diagnostic value 0x{value:04X}; fault type is unknown."
            matched.extend(selected)
        states = {r.state for r in matched}
        if len(states) > 1:
            return "Fault", "Conflicting circuit diagnoses were reported; fault type is unknown."
        if states:
            detail = "\n".join(f"Diagnostic reference 0x{r.register_reference:04X}: {r.source}" for r in matched)
            return FAULT_LABELS[next(iter(states))], detail
        return None, ""

    def view(self, key, now=None) -> ChannelView:
        now = time.monotonic() if now is None else now
        mapping = self.channels[key].mapping
        if mapping is None:
            return ChannelView("Not configured", "A verified channel mapping is required.")
        reading = self.readings.get(mapping.register_reference)
        last_value = None
        if reading and reading.value is not None:
            try:
                last_value = mapping.decode(reading.value)
            except ValueError:
                pass
        update = reading.wall_time if reading else None
        suffix = (f"Reference 0x{mapping.register_reference:04X}; PDU 0x{mapping.pdu_address:04X}.\n"
                  f"Source: {mapping.data_kind}.\n{mapping.source}")
        assumed = mapping.data_kind == "assumed_device_status"
        if assumed:
            suffix += f"\nASSUMED OUTPUT SLOT: showing mask 0x{mapping.mask:04X} from device status. Relay feedback is unverified."
        if mapping.data_kind == "zone_alarm":
            suffix += ("\nConfigured as unlatched; colors follow each fresh reported zone state."
                       if mapping.latched is False else "\nPanel zone alarm status; it may remain latched until Reset.")
        if reading and reading.value is not None:
            suffix += f"\nRaw word: 0x{reading.value:04X}."
        zone_status = ((reading.value >> (8 if mapping.data_kind == "zone_alarm" and mapping.mask == 0x0200 else 0)) & 0xFF
                       if (mapping.data_kind == "zone_alarm" or assumed) and reading and reading.value is not None else 0)
        zone_fault = bool(zone_status & 4)
        problem = self._problem(reading, now)
        if not problem and not assumed and not mapping.valid_during_module_condition and key[0] in self.summaries:
            summary = self.readings.get(self.summaries[key[0]])
            summary_problem = self._problem(summary, now)
            if summary_problem:
                problem = ("Unavailable", "Module status is not fresh; channel validity is uncertain.")
            elif summary.value & 0x18 or (summary.value & 4 and not zone_fault):
                problem = ("Unavailable", "Module reports fault, test, or disabled.")
        if problem:
            return ChannelView(problem[0], problem[1] + "\n" + suffix, last_value, update, mapping.data_kind)
        if zone_status & 0x18:
            state = "Disabled" if zone_status & 0x10 else "Test"
            subject = "device" if assumed else "zone"
            return ChannelView(state, f"The {subject} is {state.lower()}; circuit validity is uncertain.\n{suffix}",
                               None, update, mapping.data_kind)
        if mapping.fault_rules and (zone_fault or mapping.data_kind != "zone_alarm"):
            diagnosis, detail = self._diagnostic_state(mapping, now)
            if diagnosis:
                if zone_fault and diagnosis == "Unavailable":
                    diagnosis = "Fault"
                return ChannelView(diagnosis, detail + "\n" + suffix, None, update, mapping.data_kind)
        if zone_fault:
            detail = ("The device reports a fault; relay-state interpretation is unverified." if assumed else
                      "The zone reports a fault. Standard zone data does not identify short circuit, open circuit, or resistor faults.")
            return ChannelView("Fault", detail + "\n" + suffix,
                               None, update, mapping.data_kind)
        if zone_status & 1 and not zone_status & 2:
            return ChannelView("Prealarm", "Prealarm is reported.\n" + suffix, None, update, mapping.data_kind)
        try:
            active = mapping.decode(reading.value)
        except ValueError as exc:
            return ChannelView("Unavailable", f"{exc}\n{suffix}", None, update, mapping.data_kind)
        if assumed:
            state = "Set (assumed)" if active else "Clear (assumed)"
        else:
            state = ("Alarm" if active else "Normal") if mapping.data_kind == "zone_alarm" else ("Active" if active else "Inactive")
        return ChannelView(state, suffix, active, update, mapping.data_kind)
