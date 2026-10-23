"""Validated configuration with explicit channel mappings and reset targeting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import ipaddress
import json
import os
from pathlib import Path


class ConfigError(ValueError):
    pass


def number(value, name: str, minimum: int, maximum: int) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError()
        result = int(value, 16 if value.lower().startswith("0x") else 10) if isinstance(value, str) else value
        if not minimum <= result <= maximum:
            raise ValueError()
        return result
    except (TypeError, ValueError, AttributeError):
        raise ConfigError(f"{name} must be an integer from {minimum} to {maximum}.") from None


def seconds(value, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be a number from {minimum} to {maximum}.")
    return float(value)


def text(value, name: str, required=False) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ConfigError(f"{name} must be {'nonempty ' if required else ''}text.")
    return value.strip()


def flag(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false.")
    return value


def obj(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object.")
    return value


@dataclass(frozen=True)
class Connection:
    host: str = ""
    port: int = 502
    unit_id: int = 0
    panel_id: int | None = None
    initial_panel: int | None = None
    bms_mode: str = "zonepoint"
    firmware: str = ""
    timeout_seconds: float = 3.0

    def validate_endpoint(self):
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            raise ConfigError("Enter a valid panel IPv4 or IPv6 address.") from None
        number(self.port, "TCP port", 1, 65535)


@dataclass(frozen=True)
class Polling:
    request_interval_seconds: float = 1.0
    stale_min_seconds: float = 5.0
    stale_scan_multiplier: float = 2.0
    reconnect_initial_seconds: float = 2.0
    reconnect_max_seconds: float = 30.0


@dataclass(frozen=True)
class Mapping:
    register_reference: int
    mask: int
    active_value: int
    inactive_value: int
    data_kind: str
    source: str
    verified: bool = True
    latched: bool = False
    output_activation_accepted: bool = False
    valid_during_module_condition: bool = False

    @property
    def pdu_address(self):
        return self.register_reference - 1

    def decode(self, raw: int) -> bool:
        selected = raw & self.mask
        if selected == self.active_value:
            return True
        if selected == self.inactive_value:
            return False
        raise ValueError(f"Unexpected masked value 0x{selected:04X}")


@dataclass(frozen=True)
class Channel:
    channel: int
    label: str
    mapping: Mapping | None = None


@dataclass(frozen=True)
class Module:
    id: int
    name: str
    loop: int
    address: int
    inputs: tuple[Channel, ...]
    outputs: tuple[Channel, ...]


@dataclass(frozen=True)
class ResetCommand:
    enabled: bool = True
    target_panel_id: int = 0


@dataclass(frozen=True)
class Config:
    schema_version: int
    connection: Connection
    polling: Polling
    modules: tuple[Module, ...]
    reset: ResetCommand = ResetCommand()

    def channels(self):
        for module in self.modules:
            for direction in ("inputs", "outputs"):
                for channel in getattr(module, direction):
                    yield (module.id, direction, channel.channel), channel

    def summary_references(self) -> dict[int, int]:
        c = self.connection
        if c.panel_id is None or c.initial_panel is None or c.bms_mode != "zonepoint":
            return {}
        slot = c.panel_id - c.initial_panel
        return {m.id: 0x7001 + slot * 1024 + (m.loop - 1) * 256 + m.address - 1 for m in self.modules}

    def to_dict(self):
        return json.loads(json.dumps(asdict(self)))


def parse_mapping(value, direction: str, name: str) -> Mapping | None:
    if value is None:
        return None
    d = obj(value, name)
    reference = number(d.get("register_reference"), f"{name}.register_reference", 1, 65536)
    if "pdu_address" in d and number(d["pdu_address"], f"{name}.pdu_address", 0, 65535) != reference - 1:
        raise ConfigError(f"{name}: PDU address must equal register_reference minus one.")
    mask = number(d.get("mask"), f"{name}.mask", 1, 65535)
    active = number(d.get("active_value"), f"{name}.active_value", 0, 65535)
    inactive = number(d.get("inactive_value"), f"{name}.inactive_value", 0, 65535)
    if active == inactive or active & ~mask or inactive & ~mask:
        raise ConfigError(f"{name}: active/inactive values must differ and fit the mask.")
    if flag(d.get("verified", False), name + ".verified") is not True:
        raise ConfigError(f"{name}: only verified mappings may supply live states; use null until verified.")
    if flag(d.get("latched", True), name + ".latched"):
        raise ConfigError(f"{name}: a latched value cannot represent live I/O.")
    kind = d.get("data_kind")
    accepted = flag(d.get("output_activation_accepted", False), name + ".output_activation_accepted")
    allowed = ("live_input",) if direction == "inputs" else ("physical_output_feedback", "output_activation")
    if kind not in allowed:
        raise ConfigError(f"{name}.data_kind must be one of: {', '.join(allowed)}.")
    if kind == "output_activation" and not accepted:
        raise ConfigError(f"{name}: output activation requires explicit output_activation_accepted=true.")
    return Mapping(reference, mask, active, inactive, kind,
                   text(d.get("source"), name + ".source", required=True),
                   output_activation_accepted=accepted,
                   valid_during_module_condition=flag(d.get("valid_during_module_condition", False), name + ".valid_during_module_condition"))


def parse_config(value) -> Config:
    d = obj(value, "Configuration")
    if number(d.get("schema_version", 1), "schema_version", 1, 1) != 1:
        raise ConfigError("Unsupported schema version.")
    c = obj(d.get("connection", {}), "connection")
    panel = None if c.get("panel_id") is None else number(c["panel_id"], "panel_id", 1, 128)
    initial = None if c.get("initial_panel") is None else number(c["initial_panel"], "initial_panel", 1, 128)
    if (panel is None) != (initial is None):
        raise ConfigError("Set both panel_id and initial_panel, or leave both null.")
    mode = c.get("bms_mode", "zonepoint")
    if mode not in ("zonepoint", "zone"):
        raise ConfigError("bms_mode must be zonepoint or zone.")
    if panel is not None and mode == "zonepoint" and not 0 <= panel - initial < 32:
        raise ConfigError("Panel must be within the 32-node window beginning at InitPan.")
    connection = Connection(text(c.get("host", ""), "host"), number(c.get("port", 502), "port", 1, 65535),
                            number(c.get("unit_id", 0), "unit_id", 0, 255), panel, initial, mode,
                            text(c.get("firmware", ""), "firmware"),
                            seconds(c.get("timeout_seconds", 3), "timeout_seconds", 0.1, 60))
    if connection.host:
        connection.validate_endpoint()
    p = obj(d.get("polling", {}), "polling")
    polling = Polling(
        seconds(p.get("request_interval_seconds", 1), "request_interval_seconds", 1, 300),
        seconds(p.get("stale_min_seconds", 5), "stale_min_seconds", 5, 3600),
        seconds(p.get("stale_scan_multiplier", 2), "stale_scan_multiplier", 2, 100),
        seconds(p.get("reconnect_initial_seconds", 2), "reconnect_initial_seconds", 1, 300),
        seconds(p.get("reconnect_max_seconds", 30), "reconnect_max_seconds", 1, 300))
    if polling.reconnect_max_seconds < polling.reconnect_initial_seconds:
        raise ConfigError("Maximum reconnect delay must be at least the initial delay.")
    modules = []
    raw_modules = d.get("modules")
    if not isinstance(raw_modules, list) or len(raw_modules) != 4:
        raise ConfigError("Configure exactly four modules.")
    for m in raw_modules:
        m = obj(m, "Module")
        module_id = number(m.get("id"), "module.id", 1, 4)
        groups = {}
        for direction, prefix in (("inputs", "IN"), ("outputs", "OUT")):
            raw_channels = m.get(direction)
            if not isinstance(raw_channels, list) or len(raw_channels) != 4:
                raise ConfigError(f"Module {module_id}: configure exactly four {direction}.")
            channels = []
            for ch in raw_channels:
                ch = obj(ch, "Channel")
                n = number(ch.get("channel"), "channel", 1, 4)
                name = f"Module {module_id} {direction} {n}"
                channels.append(Channel(n, text(ch.get("label", f"{prefix}{n}"), name + " label", True),
                                        parse_mapping(ch.get("mapping"), direction, name)))
            if sorted(ch.channel for ch in channels) != [1, 2, 3, 4]:
                raise ConfigError(f"Module {module_id}: channel numbers must be unique, 1 through 4.")
            groups[direction] = tuple(sorted(channels, key=lambda ch: ch.channel))
        modules.append(Module(module_id, text(m.get("name", f"Module {module_id}"), "module.name", True),
                              number(m.get("loop", 1), "module.loop", 1, 4),
                              number(m.get("address"), "module.address", 1, 256), **groups))
    if sorted(m.id for m in modules) != [1, 2, 3, 4]:
        raise ConfigError("Module IDs must be unique, 1 through 4.")
    if len({(m.loop, m.address) for m in modules}) != 4:
        raise ConfigError("Each module must have a different loop/device address.")
    r = obj(d.get("reset", {}), "reset")
    reset = ResetCommand(flag(r.get("enabled", True), "reset.enabled"),
                         number(r.get("target_panel_id", 0), "reset.target_panel_id", 0, 128))
    return Config(1, connection, polling, tuple(sorted(modules, key=lambda m: m.id)), reset)


def default_config() -> Config:
    return Config(1, Connection(), Polling(), tuple(
        Module(i, f"Module {i}", 1, i,
               tuple(Channel(n, f"IN{n}") for n in range(1, 5)),
               tuple(Channel(n, f"OUT{n}") for n in range(1, 5))) for i in range(1, 5)))


def load_config(path: Path) -> Config:
    try:
        return parse_config(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot load {path.name}: {exc}") from exc


def save_config(config: Config, path: Path):
    # Atomic replacement prevents a partially written file after interruption.
    parse_config(json.loads(json.dumps(config.to_dict())))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def simulation_config(config: Config) -> Config:
    """Synthetic mappings exist in memory only; they never become a live map."""
    modules = []
    for module in config.modules:
        groups = {}
        for offset, direction in enumerate(("inputs", "outputs")):
            reference = 0x5001 + (module.id - 1) * 2 + offset
            kind = "live_input" if direction == "inputs" else "physical_output_feedback"
            groups[direction] = tuple(replace(ch, mapping=Mapping(reference, 1 << (ch.channel - 1),
                                       1 << (ch.channel - 1), 0, kind, "Synthetic demo; not a panel mapping"))
                                      for ch in getattr(module, direction))
        modules.append(replace(module, **groups))
    return replace(config, connection=replace(config.connection, panel_id=1, initial_panel=1, bms_mode="zonepoint"),
                   modules=tuple(modules), reset=ResetCommand(enabled=False))
