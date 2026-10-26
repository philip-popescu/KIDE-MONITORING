"""Run manually on a desktop: python tests/gui_smoke.py. No panel is contacted."""

from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kide_monitor.config import default_config
from kide_monitor.ui import MonitorApp


def run():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "config.json"
        app = MonitorApp(default_config(), path)
        errors = []
        app.report_callback_exception = lambda *args: errors.append(args)
        try:
            app.update()
            assert all(app.monitor.view(key).state == "Not configured" for key in app.monitor.channels)
            for mid, card in app.dashboard.cards.items():
                assert int(card.grid_info()["row"]) == (mid - 1) % 2
                assert int(card.grid_info()["column"]) == (mid - 1) // 2
            app.open_outputs()
            window = app.outputs_window
            app.open_outputs()
            assert app.outputs_window is window
            assert len(app.outputs_dashboard.cards) == 4
            app.mode_var.set("Simulation")
            app.mode_changed("Simulation")
            app.connect()
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                app.update()
                if all(app.monitor.view(key).state in ("Active", "Inactive", "Short circuit", "Open circuit", "Resistor fault") for key in app.monitor.channels):
                    break
                time.sleep(0.02)
            assert all(app.monitor.view(key).state in ("Active", "Inactive", "Short circuit", "Open circuit", "Resistor fault") for key in app.monitor.channels)
            assert app.reset_button.cget("state") == "disabled"
            assert not path.exists(), "Simulation must not save synthetic mappings"
            for card in app.dashboard.cards.values():
                for indicator in card.indicators.values():
                    assert indicator.square.winfo_width() == indicator.square.winfo_height()
            for condition, expected, color in (("Normal", "Inactive", "#219E70"), ("Alarm", "Active", "#D94452"),
                                                ("Short circuit", "Short circuit", "#F2C94C"),
                                                ("Open circuit", "Open circuit", "#3F82E9"),
                                                ("Resistor fault", "Resistor fault", "#A565D7")):
                app.input_condition.set(condition)
                app.change_simulation()
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    app.update()
                    if all(app.monitor.view((m, "inputs", ch)).state == expected for m in range(1, 5) for ch in range(1, 5)):
                        break
                    time.sleep(0.02)
                app.render()
                assert all(app.monitor.view((m, "inputs", ch)).state == expected for m in range(1, 5) for ch in range(1, 5)), condition
                for card in app.dashboard.cards.values():
                    assert all(indicator.square.cget("fg_color") == color for indicator in card.indicators.values()), condition
                assert all(app.monitor.view((m, "outputs", ch)).state in ("Active", "Inactive") for m in range(1, 5) for ch in range(1, 5))
            app.paused.set(True)
            app.change_simulation()
            # Age only one source: another source must not refresh it by accident.
            app.monitor.readings[0x5001].monotonic -= 20
            app.render()
            assert app.monitor.view((1, "inputs", 1)).state == "Stale"
            app.close_outputs()
            assert app.engine.running
            app.open_outputs()
            assert app.outputs_window is not window
            app.disconnect()
            deadline = time.monotonic() + 2
            while app.engine.running and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            assert not app.engine.running
            assert not app.monitor.connected
            app.update()
            assert app.footer.winfo_ismapped(), "Footer must remain accessible"
            app.mode_var.set("Live Modbus")
            app.mode_changed("Live Modbus")
            assert all(ch.mapping is None for ch in app.monitor.channels.values())
            assert all(app.monitor.view(key).state == "Not configured" for key in app.monitor.channels)
            app.open_settings()
            app.update()
            app.settings_window.vars["panel_id"].set("1")
            app.settings_window.vars["initial_panel"].set("1")
            app.settings_window.save(use_zone_mapping=True)
            app.update()
            assert path.exists()
            assert app.settings.connection.panel_id == 1
            assert all(ch.mapping.data_kind == "zone_alarm" and ch.mapping.latched is False
                       for m in app.settings.modules for ch in m.inputs)
            assert all(ch.mapping is None for m in app.settings.modules for ch in m.outputs)
            app.open_settings()
            app.update()
            app.settings_window.save(use_output_range=True)
            app.update()
            assert app.settings.assumed_output_count() == 16
            assert "assumed" in app.outputs_notice.cget("text")
            assert "Outputs 16/16 assumed" in app.footer.cget("text")
            app.monitor.connected = True
            for batch in app.monitor.plan:
                app.monitor.apply_read(batch.reference, [0] * batch.count)
            app.render()
            assert all(app.monitor.view((m, "outputs", ch)).state == "Clear (assumed)"
                       for m in range(1, 5) for ch in range(1, 5))
            app.monitor.apply_read(0x7001, [2])
            app.render()
            indicator = app.outputs_dashboard.cards[1].indicators[(1, "outputs", 1)]
            assert indicator.status.cget("text") == "Set (assumed)"
            assert indicator.square.cget("fg_color") == "#D94452"
            assert not errors, errors
            print("GUI smoke passed: input colors, unlatched zone profile, assumed output range/labels, windows and freshness.")
        finally:
            app.engine.stop()
            if app.engine.thread:
                app.engine.thread.join(4)
            app.destroy()


if __name__ == "__main__":
    run()
