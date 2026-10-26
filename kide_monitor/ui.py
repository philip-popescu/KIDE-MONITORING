"""CustomTkinter interface. All widget access stays on the GUI thread."""

from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
import logging
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .config import ConfigError, load_config, number, output_range_config, parse_config, save_config, simulation_config, zonepoint_config
from .engine import MonitorEngine, SIM_INPUT_CONDITIONS
from .state import MonitorState


BG = "#0B1220"
CARD = "#131F32"
BORDER = "#293950"
TEXT = "#E7EEF8"
MUTED = "#9AAFC8"
BLUE = "#397DF5"
AMBER = "#F1BC62"
COLORS = {"Active": "#D94452", "Inactive": "#219E70", "Unknown": "#485970",
          "Set (assumed)": "#D94452", "Clear (assumed)": "#219E70",
          "Alarm": "#D94452", "Normal": "#219E70", "Short circuit": "#F2C94C",
          "Open circuit": "#3F82E9", "Resistor fault": "#A565D7", "Fault": "#677489",
          "Disabled": "#677489", "Test": "#677489", "Prealarm": "#677489",
          "Not configured": "#485970", "Stale": "#677489", "Unavailable": "#677489"}


def label(parent, text="", **kwargs):
    options = dict(text=text, font=("Segoe UI", 12), text_color=TEXT, anchor="w", height=20)
    options.update(kwargs)
    return ctk.CTkLabel(parent, **options)


def button(parent, text, command, **kwargs):
    options = dict(text=text, command=command, height=34, corner_radius=7, width=90,
                   fg_color=BLUE, hover_color="#2865D0", font=("Segoe UI", 12, "bold"))
    options.update(kwargs)
    return ctk.CTkButton(parent, **options)


def clock_text(timestamp):
    return time.strftime("%H:%M:%S", time.localtime(timestamp)) if timestamp is not None else "--:--:--"


class Indicator(ctk.CTkFrame):
    def __init__(self, parent, app, key, channel):
        super().__init__(parent, fg_color="transparent")
        self.app, self.key = app, key
        self.previous = None
        label(self, channel.label, text_color=MUTED, anchor="center").pack(fill="x")
        if channel.mapping and channel.mapping.data_kind == "assumed_device_status":
            label(self, f"0x{channel.mapping.register_reference:04X}", font=("Segoe UI", 9),
                  text_color=AMBER, height=14, anchor="center").pack(fill="x")
        self.square = ctk.CTkFrame(self, width=56, height=56, corner_radius=0,
                                   fg_color=COLORS["Unknown"], border_width=1, border_color="#71819A")
        self.square.pack(pady=(6, 6))
        self.square.pack_propagate(False)
        self.number = label(self.square, f"{channel.channel:02}", font=("Segoe UI", 24, "bold"), anchor="center")
        self.number.pack(expand=True, fill="both")
        self.status = label(self, "Not configured", font=("Segoe UI", 11), anchor="center", height=18)
        self.status.pack(fill="x")
        self.updated = label(self, "--:--:--", font=("Segoe UI", 10), text_color=MUTED, anchor="center", height=16)
        self.updated.pack(fill="x")
        for widget in (self.square, self.number, self.status):
            widget.bind("<Button-1>", lambda event: app.show_channel(key), add="+")

    def render(self, view):
        current = (view.state, view.last_update)
        if current != self.previous:
            self.square.configure(fg_color=COLORS[view.state])
            self.number.configure(text_color=BG if view.state == "Short circuit" else TEXT)
            self.status.configure(text=view.state)
            self.updated.configure(text=clock_text(view.last_update))
            self.previous = current


class ModuleCard(ctk.CTkFrame):
    def __init__(self, parent, app, module, direction):
        super().__init__(parent, fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=12)
        self.module_id = module.id
        self.direction = direction
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(12, 2))
        label(header, module.name, font=("Segoe UI", 17, "bold")).pack(side="left")
        label(header, f"0{module.id}", text_color=MUTED, font=("Segoe UI", 12, "bold")).pack(side="right")
        label(self, f"Loop {module.loop}  /  Address {module.address}", text_color=MUTED,
              font=("Segoe UI", 11)).pack(fill="x", padx=20)
        channels = ctk.CTkFrame(self, fg_color="transparent")
        channels.pack(fill="both", expand=True, padx=10, pady=(5, 0))
        channels.grid_rowconfigure(0, weight=1)
        self.indicators = {}
        for i, channel in enumerate(getattr(module, direction)):
            channels.grid_columnconfigure(i, weight=1, uniform="channels")
            key = (module.id, direction, channel.channel)
            indicator = Indicator(channels, app, key, channel)
            indicator.grid(row=0, column=i, sticky="nsew", padx=2)
            self.indicators[key] = indicator
        self.condition = label(self, "Waiting for data", text_color=MUTED, font=("Segoe UI", 10), height=16)
        self.condition.pack(fill="x", padx=20, pady=(3, 8))

    def render(self, monitor, now):
        views = []
        for key, indicator in self.indicators.items():
            view = monitor.view(key, now)
            indicator.render(view)
            views.append(view)
        assumed = self.direction == "outputs" and any(monitor.channels[key].mapping and
                      monitor.channels[key].mapping.data_kind == "assumed_device_status" for key in self.indicators)
        conditions = monitor.module_conditions(self.module_id, now)
        if assumed:
            text, color = "Assumed device-status bits / relay feedback unverified", AMBER
        elif conditions:
            text = " / ".join(conditions)
            color = AMBER
        elif all(v.state == "Not configured" for v in views):
            text, color = "Verified channel mappings required", MUTED
        elif self.module_id not in monitor.summaries:
            text, color = "Module summary not configured", MUTED
        else:
            text, color = "No module condition reported", MUTED
        self.condition.configure(text=text, text_color=color)
        return views


class Dashboard(ctk.CTkScrollableFrame):
    def __init__(self, parent, app, direction):
        super().__init__(parent, fg_color="transparent")
        self.app, self.direction = app, direction
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        label(header, direction.capitalize(), font=("Segoe UI", 23, "bold")).pack(side="left")
        self.subtitle = label(header, "Live channel activity", text_color=MUTED)
        self.subtitle.pack(side="left", padx=14)
        self.stats = label(header, "16 channels", text_color=MUTED)
        self.stats.pack(side="right")
        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.pack(fill="both", expand=True)
        for i in range(2):
            self.grid_frame.grid_columnconfigure(i, weight=1, uniform="modules")
            self.grid_frame.grid_rowconfigure(i, weight=1, uniform="modules")
        self.cards = {}
        self.rebuild()

    def rebuild(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.cards = {}
        zone_inputs = self.direction == "inputs" and any(
            ch.mapping and ch.mapping.data_kind == "zone_alarm"
            for _, ch in self.app.monitor.config.channels())
        assumed = self.direction == "outputs" and self.app.monitor.config.assumed_output_count()
        self.subtitle.configure(text="Assumed device-status bits" if assumed else
                                "Zone alarms & circuit status" if zone_inputs else "Channel activity & status")
        for module in self.app.monitor.config.modules:
            card = ModuleCard(self.grid_frame, self.app, module, self.direction)
            row, column = (module.id - 1) % 2, (module.id - 1) // 2
            card.grid(row=row, column=column, sticky="nsew",
                      padx=(0, 7) if column == 0 else (7, 0), pady=(0, 7) if row == 0 else (7, 0))
            self.cards[module.id] = card

    def render(self, now):
        views = [v for card in self.cards.values() for v in card.render(self.app.monitor, now)]
        active = sum(v.state in ("Active", "Alarm", "Set (assumed)") for v in views)
        inactive = sum(v.state in ("Inactive", "Normal", "Clear (assumed)") for v in views)
        faults = sum(v.state in ("Short circuit", "Open circuit", "Resistor fault", "Fault") for v in views)
        assumed = self.direction == "outputs" and self.app.monitor.config.assumed_output_count()
        self.stats.configure(text=f"{active} {'set' if assumed else 'active'}  /  {inactive} {'clear' if assumed else 'normal'}"
                             f"  /  {faults} fault  /  {16 - active - inactive - faults} unknown")
        return views


def legend(parent, inputs=False):
    row = ctk.CTkFrame(parent, fg_color="transparent")
    entries = [(COLORS["Inactive"], "Normal / inactive"), (COLORS["Active"], "Alarm / active")]
    if inputs:
        entries.extend((COLORS[name], name) for name in ("Short circuit", "Open circuit", "Resistor fault"))
    entries.append((COLORS["Unknown"], "Unknown / other fault"))
    for color, name in entries:
        ctk.CTkFrame(row, width=10, height=10, corner_radius=0, fg_color=color).pack(side="left", padx=(0, 6))
        label(row, name, text_color=MUTED, font=("Segoe UI", 11)).pack(side="left", padx=(0, 14))
    return row


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Connection settings")
        self.geometry("620x700")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.transient(app)
        self.vars = {}
        label(self, "Connection settings", font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=26, pady=(20, 12))
        form = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        form.pack(fill="both", expand=True, padx=26)
        form.grid_columnconfigure(1, weight=1)
        c = app.settings.connection
        fields = [("unit_id", "Modbus unit ID", str(c.unit_id)),
                  ("panel_id", "Panel node ID (optional)", "" if c.panel_id is None else str(c.panel_id)),
                  ("initial_panel", "BMS InitPan (optional)", "" if c.initial_panel is None else str(c.initial_panel)),
                  ("firmware", "Panel firmware (optional)", c.firmware),
                  ("timeout_seconds", "Request timeout (seconds)", str(c.timeout_seconds)),
                  ("target_panel_id", "Reset target (0 = local panel)", str(app.settings.reset.target_panel_id))]
        for row, (key, title, value) in enumerate(fields):
            label(form, title).grid(row=row, column=0, sticky="w", padx=18, pady=8)
            self.vars[key] = tk.StringVar(value=value)
            ctk.CTkEntry(form, textvariable=self.vars[key], width=200).grid(row=row, column=1, sticky="ew", padx=18, pady=8)
        label(form, "BMS mode").grid(row=6, column=0, sticky="w", padx=18, pady=8)
        self.mode = ctk.CTkOptionMenu(form, values=["zonepoint", "zone"])
        self.mode.set(c.bms_mode)
        self.mode.grid(row=6, column=1, sticky="ew", padx=18, pady=8)
        self.reset_enabled = tk.BooleanVar(value=app.settings.reset.enabled)
        ctk.CTkCheckBox(form, text="Enable panel reset", variable=self.reset_enabled).grid(row=7, column=0, columnspan=2, sticky="w", padx=18, pady=12)
        label(form, "Set both panel ID and InitPan to read module summaries.\nIndividual channel sources are loaded from a configuration file.",
              text_color=MUTED, justify="left", wraplength=510).grid(row=8, column=0, columnspan=2, sticky="w", padx=18, pady=12)
        counts = app.settings.mapped_counts()
        qualifier = " (assumed)" if app.settings.assumed_output_count() else ""
        label(self, f"Inputs {counts['inputs']}/16  |  Outputs {counts['outputs']}/16{qualifier}  |  {app.config_path.name}",
              text_color=MUTED).pack(anchor="w", padx=26, pady=(10, 0))
        presets = ctk.CTkFrame(self, fg_color="transparent")
        presets.pack(fill="x", padx=26, pady=(12, 0))
        button(presets, "Load configuration...", self.import_config, width=170, fg_color=BORDER).pack(side="left")
        button(presets, "Use zones 1-16", lambda: self.save(use_zone_mapping=True), width=130,
               fg_color=BORDER).pack(side="left", padx=8)
        button(presets, "Use output range", lambda: self.save(use_output_range=True), width=140,
               fg_color=BORDER).pack(side="left")
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=26, pady=14)
        button(buttons, "Save", self.save).pack(side="right")
        button(buttons, "Cancel", self.destroy, fg_color=BORDER).pack(side="right", padx=8)
        self.after(100, self.grab_set)

    def import_config(self):
        self.grab_release()
        if self.app.import_config():
            self.destroy()
        else:
            self.grab_set()

    def save(self, use_zone_mapping=False, use_output_range=False):
        try:
            d = self.app.settings.to_dict()
            d["connection"].update(host=self.app.host.get().strip(), port=self.app.port.get().strip(),
                                    bms_mode=self.mode.get())
            for key in ("unit_id", "panel_id", "initial_panel", "firmware", "timeout_seconds"):
                value = self.vars[key].get().strip()
                if key in ("panel_id", "initial_panel"):
                    value = None if not value else number(value, key, 1, 128)
                elif key == "timeout_seconds":
                    try:
                        value = float(value)
                    except ValueError:
                        raise ConfigError("Timeout must be a number.") from None
                d["connection"][key] = value
            d["reset"] = dict(enabled=self.reset_enabled.get(), target_panel_id=self.vars["target_panel_id"].get())
            config = parse_config(d)
            if use_zone_mapping:
                config = zonepoint_config(config)
            if use_output_range:
                config = output_range_config(config, replace_existing=True)
            save_config(config, self.app.config_path)
            self.app.apply_config(config)
        except (ConfigError, OSError) as exc:
            messagebox.showerror("Cannot save settings", str(exc), parent=self)
            return
        self.destroy()


class MonitorApp(ctk.CTk):
    def __init__(self, config, config_path: Path, simulate=False):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.settings, self.config_path = config, config_path
        self.monitor = MonitorState(config)
        self.events = Queue()
        self.engine = MonitorEngine(self.events)
        self.session = 0
        self.outputs_window = self.outputs_dashboard = self.settings_window = None
        self.closing = False
        self.last_error = ""
        self.reset_result = ""
        self.status_text = "Disconnected"
        self.title("KIDE Monitoring - Inputs")
        available_height = int((self.winfo_screenheight() - 100) / self._get_window_scaling())
        self.geometry(f"1120x{min(870, available_height)}+30+20")
        self.minsize(1010, min(780, available_height))
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.host = tk.StringVar(value=config.connection.host)
        self.port = tk.StringVar(value=str(config.connection.port))
        self.mode_var = tk.StringVar(value="Simulation" if simulate else "Live Modbus")
        self.paused = tk.BooleanVar(value=False)
        self.outage = tk.BooleanVar(value=False)
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=24, pady=18)
        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        label(header, "KIDE", font=("Segoe UI", 27, "bold")).pack(side="left")
        label(header, "CONTROL PANEL MONITOR", text_color=MUTED, font=("Segoe UI", 11, "bold")).pack(side="left", padx=16)
        self.connection_badge = label(header, "DISCONNECTED", text_color=MUTED, font=("Segoe UI", 12, "bold"))
        self.connection_badge.pack(side="right")
        toolbar = ctk.CTkFrame(shell, fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=10)
        toolbar.pack(fill="x", pady=(0, 10))
        inner = ctk.CTkFrame(toolbar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)
        for col, title in enumerate(("Panel IP address", "TCP port", "Data source")):
            label(inner, title, text_color=MUTED, font=("Segoe UI", 10)).grid(row=0, column=col, sticky="w", padx=(0, 8))
        self.host_entry = ctk.CTkEntry(inner, textvariable=self.host, width=158, height=34)
        self.host_entry.grid(row=1, column=0, padx=(0, 8))
        self.port_entry = ctk.CTkEntry(inner, textvariable=self.port, width=60, height=34)
        self.port_entry.grid(row=1, column=1, padx=(0, 8))
        self.mode = ctk.CTkOptionMenu(inner, values=["Live Modbus", "Simulation"], variable=self.mode_var,
                                      command=self.mode_changed, width=130, height=34, fg_color=BORDER)
        self.mode.grid(row=1, column=2, padx=(0, 12))
        self.connect_button = button(inner, "Connect", self.connect, width=80)
        self.connect_button.grid(row=1, column=3, padx=(0, 6))
        self.disconnect_button = button(inner, "Disconnect", self.disconnect, width=88, fg_color=BORDER)
        self.disconnect_button.grid(row=1, column=4, padx=(0, 6))
        button(inner, "Outputs", self.open_outputs, width=78, fg_color=BORDER).grid(row=1, column=5, padx=(0, 6))
        self.settings_button = button(inner, "Settings", self.open_settings, width=78, fg_color=BORDER)
        self.settings_button.grid(row=1, column=6, padx=(0, 6))
        self.reset_button = button(inner, "Reset panel", self.reset_panel, width=96, fg_color="#8F3948", hover_color="#AF4254")
        self.reset_button.grid(row=1, column=7)
        self.reset_hint = label(toolbar, "Connect to enable the panel reset command.", text_color=MUTED, font=("Segoe UI", 10))
        self.reset_hint.pack(anchor="w", padx=14, pady=(0, 10))
        self.simulation_bar = ctk.CTkFrame(shell, fg_color="#342B1B", corner_radius=8)
        label(self.simulation_bar, "SIMULATION  /  Synthetic signals; no panel connection", text_color=AMBER,
              font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
        simulation_controls = ctk.CTkFrame(self.simulation_bar, fg_color="transparent")
        simulation_controls.pack(fill="x", padx=12, pady=6)
        ctk.CTkCheckBox(simulation_controls, text="Pause readings", variable=self.paused, command=self.change_simulation,
                        font=("Segoe UI", 11), checkbox_width=18, checkbox_height=18).pack(side="left", padx=(0, 20))
        ctk.CTkCheckBox(simulation_controls, text="Connection outage", variable=self.outage, command=self.change_simulation,
                        font=("Segoe UI", 11), checkbox_width=18, checkbox_height=18).pack(side="left", padx=(0, 20))
        label(simulation_controls, "Module fault:", text_color=AMBER, font=("Segoe UI", 11)).pack(side="left", padx=(0, 8))
        self.fault = ctk.CTkOptionMenu(simulation_controls, values=["None", "1", "2", "3", "4"], width=75, height=26,
                                       command=lambda _: self.change_simulation())
        self.fault.pack(side="left")
        label(simulation_controls, "Input:", text_color=AMBER, font=("Segoe UI", 11)).pack(side="left", padx=(14, 8))
        self.input_condition = ctk.CTkOptionMenu(simulation_controls, values=list(SIM_INPUT_CONDITIONS),
                                               width=140, height=26, command=lambda _: self.change_simulation())
        self.input_condition.pack(side="left")
        button(simulation_controls, "Advance signals", self.advance_simulation, width=120, height=26, fg_color=BORDER).pack(side="right")
        self.dashboard_slot = ctk.CTkFrame(shell, fg_color="transparent", corner_radius=0)
        self.dashboard_slot.pack(fill="both", expand=True, pady=(10, 0))
        self.dashboard = Dashboard(self.dashboard_slot, self, "inputs")
        self.dashboard.pack(fill="both", expand=True)
        legend(shell, inputs=True).pack(fill="x", pady=(10, 4))
        self.notice = label(shell, "", text_color=AMBER, font=("Segoe UI", 11), wraplength=1030, justify="left")
        self.notice.pack(fill="x")
        self.footer = label(shell, "", text_color=MUTED, font=("Segoe UI", 10))
        self.footer.pack(fill="x", pady=(2, 0))
        self.mode_changed(self.mode_var.get())
        self.after(100, self.pump)
        if simulate:
            self.after(250, self.connect)

    def apply_config(self, config):
        self.settings = config
        self.host.set(config.connection.host)
        self.port.set(str(config.connection.port))
        effective = simulation_config(config) if self.mode_var.get() == "Simulation" else config
        self.monitor = MonitorState(effective)
        self.session = 0
        self.last_error = ""
        self.dashboard.rebuild()
        if self.outputs_dashboard:
            self.outputs_dashboard.rebuild()

    def mode_changed(self, value):
        if self.engine.running:
            return
        effective = simulation_config(self.settings) if value == "Simulation" else self.settings
        self.monitor = MonitorState(effective)
        self.session = 0
        self.last_error = self.reset_result = ""
        self.dashboard.rebuild()
        if self.outputs_dashboard:
            self.outputs_dashboard.rebuild()
        if value == "Simulation":
            self.simulation_bar.pack(before=self.dashboard_slot, fill="x", pady=(0, 0))
        else:
            self.simulation_bar.pack_forget()
        self.render()

    def connect(self):
        try:
            if self.engine.running:
                return
            simulated = self.mode_var.get() == "Simulation"
            c = replace(self.settings.connection, host=self.host.get().strip(), port=number(self.port.get().strip(), "Port", 1, 65535))
            if not simulated:
                c.validate_endpoint()
            # Simulation must not change the real connection or channel mapping.
            if not simulated:
                self.settings = replace(self.settings, connection=c)
                save_config(self.settings, self.config_path)
            effective = simulation_config(self.settings) if simulated else self.settings
            self.monitor = MonitorState(effective)
            self.dashboard.rebuild()
            if self.outputs_dashboard:
                self.outputs_dashboard.rebuild()
            self.last_error = ""
            self.reset_result = ""
            self.paused.set(False)
            self.outage.set(False)
            self.fault.set("None")
            self.input_condition.set("Auto")
            self.status_text = "Connecting"
            self.session = self.engine.start(effective, simulated)
            self.render()
        except (ConfigError, OSError, RuntimeError) as exc:
            messagebox.showerror("Cannot connect", str(exc), parent=self)

    def disconnect(self):
        self.engine.stop()
        self.monitor.disconnect()
        self.status_text = "Disconnecting" if self.engine.running else "Disconnected"
        self.render()

    def import_config(self):
        if self.engine.running:
            return False
        path = filedialog.askopenfilename(parent=self, title="Load verified configuration", filetypes=[("JSON configuration", "*.json")])
        if not path:
            return False
        try:
            config = load_config(Path(path))
        except ConfigError as exc:
            messagebox.showerror("Cannot load configuration", str(exc), parent=self)
            return False
        self.config_path = Path(path)
        self.apply_config(config)
        self.render()
        return True

    def open_settings(self):
        if self.engine.running:
            return
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsDialog(self)

    def open_outputs(self):
        if self.outputs_window and self.outputs_window.winfo_exists():
            self.outputs_window.deiconify()
            self.outputs_window.lift()
            self.outputs_window.focus_set()
            return
        window = self.outputs_window = ctk.CTkToplevel(self)
        window.title("KIDE Monitoring - Outputs")
        window.geometry("1000x710")
        window.minsize(900, 690)
        window.configure(fg_color=BG)
        window.protocol("WM_DELETE_WINDOW", self.close_outputs)
        shell = ctk.CTkFrame(window, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=24, pady=20)
        self.outputs_mode = label(shell, "", text_color=AMBER, font=("Segoe UI", 11, "bold"))
        self.outputs_mode.pack(fill="x", pady=(0, 10))
        self.outputs_dashboard = Dashboard(shell, self, "outputs")
        self.outputs_dashboard.pack(fill="both", expand=True)
        legend(shell).pack(fill="x", pady=(10, 0))
        self.outputs_notice = label(shell, "", text_color=AMBER, font=("Segoe UI", 11),
                                    wraplength=900, justify="left")
        self.outputs_notice.pack(fill="x")
        self.outputs_footer = label(shell, "", text_color=MUTED, font=("Segoe UI", 10))
        self.outputs_footer.pack(fill="x")
        self.render()

    def close_outputs(self):
        self.outputs_window.destroy()
        self.outputs_window = self.outputs_dashboard = None

    def show_channel(self, key):
        channel = self.monitor.channels[key]
        view = self.monitor.view(key)
        if view.source_kind == "zone_alarm":
            last = "Unknown" if view.last_value is None else ("Alarm" if view.last_value else "No alarm")
        elif view.source_kind == "assumed_device_status":
            last = "Unknown" if view.last_value is None else ("Bit set (assumed)" if view.last_value else "Bit clear (assumed)")
        else:
            last = "Unknown" if view.last_value is None else ("Active" if view.last_value else "Inactive")
        messagebox.showinfo(f"Module {key[0]} / {channel.label}",
                            f"State: {view.state}\nLast known: {last}\nLast reading: {clock_text(view.last_update)}\n\n{view.detail}", parent=self)

    def reset_panel(self):
        if not self.engine.connected.is_set() or self.engine.simulated or self.engine.reset_pending.is_set() or not self.settings.reset.enabled:
            return
        target = self.settings.reset.target_panel_id
        name = "local panel" if target == 0 else f"panel ID {target}"
        if not messagebox.askokcancel("Reset panel", f"Send Execute Reset to {name} at\n{self.settings.connection.host}:{self.settings.connection.port}?\n\nThis resets the panel's alarm/status conditions.", icon="warning", parent=self):
            return
        try:
            self.engine.request_reset()
            self.reset_result = "Reset queued. Waiting for acknowledgement."
        except RuntimeError as exc:
            messagebox.showwarning("Reset not sent", str(exc), parent=self)
        self.render()

    def change_simulation(self):
        controls = self.engine.simulation
        (controls.paused.set if self.paused.get() else controls.paused.clear)()
        (controls.outage.set if self.outage.get() else controls.outage.clear)()
        controls.fault_module = 0 if self.fault.get() == "None" else int(self.fault.get())
        controls.input_condition = self.input_condition.get()

    def advance_simulation(self):
        self.engine.simulation.step += 1

    def pump(self):
        if self.closing:
            return
        for _ in range(200):
            try:
                session, kind, payload = self.events.get_nowait()
            except Empty:
                break
            if session != self.session:
                continue
            if kind == "status":
                self.status_text = payload["text"]
                if payload["connected"] and not self.engine.stop_event.is_set():
                    self.monitor.connected = True
                else:
                    self.monitor.disconnect()
            elif kind == "sample":
                self.monitor.apply_read(payload["reference"], payload["values"], payload["monotonic"], payload["wall_time"])
            elif kind == "read_error":
                self.monitor.fail_read(payload["reference"], payload["count"], payload["message"])
            elif kind == "error":
                self.last_error = payload["message"]
            elif kind == "reset":
                self.reset_result = payload["message"]
                if payload["outcome"] in ("acknowledged", "unknown"):
                    self.monitor.disconnect()
                if payload["outcome"] in ("unknown", "rejected"):
                    messagebox.showwarning("Panel reset", payload["message"], parent=self)
        self.render()
        self.after(200, self.pump)

    def render(self):
        now = time.monotonic()
        input_views = self.dashboard.render(now)
        simulated = self.mode_var.get() == "Simulation"
        running = self.engine.running
        self.connect_button.configure(state="disabled" if running else "normal")
        self.disconnect_button.configure(state="normal" if running and not self.engine.stop_event.is_set() else "disabled")
        self.settings_button.configure(state="disabled" if running else "normal")
        self.mode.configure(state="disabled" if running else "normal")
        self.host_entry.configure(state="disabled" if running or simulated else "normal")
        self.port_entry.configure(state="disabled" if running or simulated else "normal")
        enabled = self.engine.connected.is_set() and not simulated and self.settings.reset.enabled and not self.engine.reset_pending.is_set()
        self.reset_button.configure(state="normal" if enabled else "disabled")
        target = self.settings.reset.target_panel_id
        target_name = "local panel" if target == 0 else f"panel ID {target}"
        hint = ("Panel reset is disabled in simulation." if simulated else
                "Panel reset is disabled in Settings." if not self.settings.reset.enabled else
                f"Reset target: {target_name}. Confirmation is required." if self.engine.connected.is_set() else
                "Connect to enable the panel reset command.")
        self.reset_hint.configure(text=self.reset_result or hint)
        self.connection_badge.configure(text=self.status_text.upper(), text_color=AMBER if simulated else ("#68DBAD" if self.monitor.connected else MUTED))
        counts = self.monitor.config.mapped_counts()
        assumed_outputs = self.monitor.config.assumed_output_count()
        missing_inputs, missing_outputs = 16 - counts["inputs"], 16 - counts["outputs"]
        notice_color = AMBER
        if self.last_error:
            notice = self.last_error
        elif simulated:
            notice = "Readings paused; values will become stale." if self.paused.get() else "Simulation data only. Live channel mappings remain unchanged."
        elif missing_inputs:
            notice = f"{missing_inputs} input channels are not configured. Use Settings > Use zones 1-16 or load an input configuration."
        elif missing_outputs:
            notice = f"All 16 inputs are configured. The {missing_outputs} outputs are not configured."
            notice_color = MUTED
        elif assumed_outputs:
            notice = f"Inputs are configured. {assumed_outputs} outputs use assumed device-status bits; relay feedback is unverified."
        else:
            notice = "All inputs and outputs are configured."
            notice_color = MUTED
        if not simulated and any(v.state == "Fault" for v in input_views):
            notice += " Input fault type is unavailable; those faults remain gray. Click a channel for details."
            notice_color = AMBER
        self.notice.configure(text=notice, text_color=notice_color)
        qualifier = " assumed" if assumed_outputs else ""
        footer = (f"Last reading  {clock_text(self.monitor.last_update)}     |     Inputs {counts['inputs']}/16"
                  f"     |     Outputs {counts['outputs']}/16{qualifier}     |     Planned scan  {self.monitor.scan_seconds:g}s"
                  f"     |     Stale after  {self.monitor.stale_seconds:g}s")
        self.footer.configure(text=footer)
        if self.outputs_dashboard:
            self.outputs_dashboard.render(now)
            self.outputs_mode.configure(text="SIMULATION / Synthetic output signals" if simulated else self.status_text)
            self.outputs_notice.configure(text=(
                "Simulation outputs only; live output mappings are unchanged." if simulated else
                f"{missing_outputs} output channels need verified read mappings. This does not block input monitoring."
                if missing_outputs else
                f"{assumed_outputs} output slots use the assumed device-status range. Set/clear labels show selected bits, not verified relay states."
                if assumed_outputs else "All 16 output channels are configured."))
            self.outputs_footer.configure(text=footer)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.engine.stop()
        self.monitor.disconnect()
        self.title("KIDE Monitoring - Closing")
        self.close_deadline = time.monotonic() + 4
        self.finish_close()

    def finish_close(self):
        if self.engine.running and time.monotonic() < self.close_deadline:
            self.after(50, self.finish_close)
        else:
            self.destroy()

    def report_callback_exception(self, exception, value, traceback):
        logging.getLogger(__name__).error("GUI callback failed", exc_info=(exception, value, traceback))
        messagebox.showerror("Application error", str(value), parent=self)
