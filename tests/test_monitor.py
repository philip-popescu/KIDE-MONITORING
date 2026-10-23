from dataclasses import replace
from pathlib import Path
from queue import Queue
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pymodbus.pdu import ExceptionResponse
from pymodbus.pdu.register_message import ReadHoldingRegistersResponse, WriteSingleRegisterResponse

from kide_monitor.config import ConfigError, default_config, load_config, parse_config, save_config, simulation_config
from kide_monitor.engine import MonitorEngine, RequestLimiter
from kide_monitor.modbus import ModbusException, PanelClient, ProtocolError
from kide_monitor.state import MonitorState, read_plan


class ConfigurationTests(unittest.TestCase):
    def test_frozen_app_finds_configuration_beside_executable(self):
        import main
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "KIDE_Monitoring.exe"
            save_config(default_config(), executable.with_name("config.json"))
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(executable)), \
                 patch.object(sys, "argv", [str(executable), "--validate-config"]):
                self.assertEqual(0, main.main())

    def test_round_trip_preserves_unmapped_channels_and_reset(self):
        config = default_config()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            save_config(config, path)
            self.assertEqual(config, load_config(path))
        self.assertEqual(32, sum(ch.mapping is None for _, ch in config.channels()))
        self.assertEqual(0, config.reset.target_panel_id)

    def test_rejects_invalid_endpoint_timing_and_broadcast_reset(self):
        for group, key, value in (("connection", "host", "bad-host"), ("connection", "port", 65536),
                                  ("polling", "request_interval_seconds", 0.5),
                                  ("reset", "target_panel_id", 65535)):
            with self.subTest(key=key):
                raw = default_config().to_dict()
                raw[group][key] = value
                with self.assertRaises(ConfigError):
                    parse_config(raw)

    def test_rejects_unverified_latched_and_unaccepted_output_sources(self):
        base = simulation_config(default_config()).to_dict()
        for direction, key, value in (("inputs", "verified", False), ("inputs", "latched", True),
                                      ("inputs", "pdu_address", 123), ("outputs", "data_kind", "output_activation")):
            raw = simulation_config(default_config()).to_dict()
            raw["modules"][0][direction][0]["mapping"][key] = value
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse_config(raw)
        base["modules"][0]["outputs"][0]["mapping"].update(data_kind="output_activation", output_activation_accepted=True)
        self.assertEqual("output_activation", parse_config(base).modules[0].outputs[0].mapping.data_kind)

    def test_node_offset_and_grouping(self):
        raw = default_config().to_dict()
        raw["connection"].update(panel_id=10, initial_panel=9)
        config = parse_config(raw)
        self.assertEqual(0x7401, config.summary_references()[1])
        self.assertEqual([(0x7400, 4)], [(b.pdu_address, b.count) for b in read_plan(config)])
        plan = read_plan(simulation_config(config))
        self.assertTrue(all(b.count <= 4 for b in plan))
        self.assertEqual(3, len(plan))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.config = simulation_config(default_config())
        self.state = MonitorState(self.config)
        self.state.connected = True
        for batch in self.state.plan:
            self.state.apply_read(batch.reference, [0] * batch.count, 100, 1000)

    def test_independent_channels(self):
        self.state.apply_read(0x5001, [4], 100, 1000)
        self.assertEqual("Active", self.state.view((1, "inputs", 3), 101).state)
        for key in ((1, "inputs", 1), (1, "inputs", 2), (1, "outputs", 3), (2, "inputs", 3)):
            self.assertEqual("Inactive", self.state.view(key, 101).state)

    def test_partial_failure_and_freshness_are_source_specific(self):
        self.state.fail_read(0x5001, 1, "Illegal data address")
        self.state.apply_read(0x5003, [1], 104, 1004)
        self.assertEqual("Unavailable", self.state.view((1, "inputs", 1), 104).state)
        self.assertEqual("Active", self.state.view((2, "inputs", 1), 104).state)
        self.state.apply_read(0x7001, [0] * 4, 106, 1006)
        self.assertEqual("Stale", self.state.view((1, "outputs", 1), 106).state)
        self.assertEqual("Active", self.state.view((2, "inputs", 1), 106).state)

    def test_module_fault_never_becomes_green(self):
        self.state.apply_read(0x7001, [4], 101, 1001)
        self.assertEqual(("Fault",), self.state.module_conditions(1, 101))
        self.assertEqual("Unavailable", self.state.view((1, "inputs", 1), 101).state)
        self.assertEqual("Inactive", self.state.view((2, "inputs", 1), 101).state)

    def test_disconnect_requires_new_readings(self):
        self.state.disconnect()
        self.state.connected = True
        self.assertEqual("Unavailable", self.state.view((1, "inputs", 1), 101).state)
        self.state.apply_read(0x5001, [0], 102, 1002)
        self.assertEqual("Unavailable", self.state.view((1, "inputs", 1), 102).state)
        self.state.apply_read(0x7001, [0], 102, 1002)
        self.assertEqual("Inactive", self.state.view((1, "inputs", 1), 102).state)

    def test_unmapped_and_unexpected_values_are_never_inactive(self):
        self.assertEqual("Not configured", MonitorState(default_config()).view((1, "inputs", 1)).state)
        raw = self.config.to_dict()
        raw["modules"][0]["inputs"][0]["mapping"].update(mask=3, active_value=1, inactive_value=0)
        state = MonitorState(parse_config(raw))
        state.connected = True
        state.apply_read(0x5001, [2], 100, 1000)
        state.apply_read(0x7001, [0], 100, 1000)
        self.assertEqual("Unavailable", state.view((1, "inputs", 1), 101).state)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("kide_monitor.modbus.PymodbusTcpClient")
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = self.factory.return_value
        self.backend.connected = True
        self.client = PanelClient("127.0.0.1")

    def test_uses_pymodbus_without_internal_retries(self):
        self.assertEqual(0, self.factory.call_args.kwargs["retries"])
        self.backend.read_holding_registers.return_value = ReadHoldingRegistersResponse(dev_id=0, registers=[11, 22])
        self.assertEqual([11, 22], self.client.read_holding_registers(0x7000, 2))
        self.backend.read_holding_registers.assert_called_once_with(address=0x7000, count=2, device_id=0)

    def test_read_limits_malformed_response_and_modbus_exception(self):
        with self.assertRaises(ValueError):
            self.client.read_holding_registers(0, 5)
        self.backend.read_holding_registers.return_value = ReadHoldingRegistersResponse(dev_id=0, registers=[1])
        with self.assertRaises(ProtocolError):
            self.client.read_holding_registers(0, 2)
        self.backend.read_holding_registers.return_value = ExceptionResponse(3, 2)
        with self.assertRaises(ModbusException):
            self.client.read_holding_registers(0, 1)

    def test_reset_exact_address_payload_and_echo(self):
        self.backend.write_register.return_value = WriteSingleRegisterResponse(dev_id=0, address=0, registers=[12])
        self.client.execute_reset(12)
        self.backend.write_register.assert_called_once_with(address=0, value=12, device_id=0)
        with self.assertRaises(ValueError):
            self.client.execute_reset(65535)
        self.backend.write_register.return_value = WriteSingleRegisterResponse(dev_id=0, address=1, registers=[12])
        with self.assertRaises(ProtocolError):
            self.client.execute_reset(12)


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for worker")
        time.sleep(0.01)


class WorkerTests(unittest.TestCase):
    def stop_engine(self, engine):
        engine.stop()
        engine.thread.join(4)
        self.assertFalse(engine.running)

    def test_limiter_is_interruptible_and_has_one_second_minimum(self):
        clock = [10.0]
        waits = []
        class Stop:
            def wait(self, delay):
                waits.append(delay)
                clock[0] += delay
                return False
        limiter = RequestLimiter(lambda: clock[0])
        self.assertTrue(limiter.wait(Stop(), 0.1))
        self.assertTrue(limiter.wait(Stop(), 0.1))
        self.assertEqual([0, 1], waits)
        stopped = threading.Event()
        stopped.set()
        self.assertFalse(limiter.wait(stopped, 1))

    def test_real_spacing_applies_to_reads_and_reset(self):
        calls = []
        class Client:
            def connect(self): pass
            def close(self): pass
            def read_holding_registers(self, address, count):
                calls.append(("read", time.monotonic(), count))
                return [0] * count
            def execute_reset(self, target):
                calls.append(("reset", time.monotonic(), target))
        engine = MonitorEngine(Queue(), lambda config: Client())
        # Simulation config disables reset; opt in explicitly for this fake transport.
        config = replace(simulation_config(default_config()), reset=default_config().reset)
        engine.start(config)
        try:
            wait_for(lambda: len(calls) >= 1)
            engine.request_reset()
            wait_for(lambda: any(c[0] == "reset" for c in calls))
            self.assertTrue(all(b[1] - a[1] >= 0.98 for a, b in zip(calls, calls[1:])))
            self.assertTrue(all(c[2] <= 4 for c in calls if c[0] == "read"))
            self.assertEqual(1, sum(c[0] == "reset" for c in calls))
        finally:
            self.stop_engine(engine)

    def test_lost_reset_acknowledgement_is_never_replayed(self):
        calls = []
        events = Queue()
        class Client:
            def connect(self): calls.append("connect")
            def close(self): pass
            def execute_reset(self, target):
                calls.append("reset")
                raise OSError("Acknowledgement lost")
        config = default_config()
        config = replace(config, polling=replace(config.polling, reconnect_initial_seconds=1, reconnect_max_seconds=1))
        engine = MonitorEngine(events, lambda c: Client())
        engine.start(config)
        try:
            wait_for(engine.connected.is_set)
            engine.request_reset()
            wait_for(lambda: calls.count("connect") >= 2)
            self.assertEqual(1, calls.count("reset"))
            payloads = list(events.queue)
            self.assertTrue(any(kind == "reset" and p["outcome"] == "unknown" for _, kind, p in payloads))
        finally:
            self.stop_engine(engine)


class LocalTcpTests(unittest.TestCase):
    def test_actual_pymodbus_read_and_reset_frames(self):
        requests, errors = [], []
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(3)
        def receive(connection, count):
            result = b""
            while len(result) < count:
                part = connection.recv(count - len(result))
                if not part:
                    raise OSError("Incomplete test request")
                result += part
            return result
        def serve():
            try:
                with server.accept()[0] as connection:
                    connection.settimeout(3)
                    for _ in range(2):
                        header = receive(connection, 7)
                        tid, protocol, length, unit = struct.unpack(">HHHB", header)
                        request = receive(connection, length - 1)
                        requests.append((unit, request))
                        response = b"\x03\x08" + struct.pack(">4H", 1, 2, 3, 4) if request[0] == 3 else request
                        packet = struct.pack(">HHHB", tid, protocol, len(response) + 1, unit) + response
                        # Exercise the actual library's TCP reassembly too.
                        connection.sendall(packet[:4])
                        connection.sendall(packet[4:])
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        client = PanelClient("127.0.0.1", server.getsockname()[1])
        try:
            client.connect()
            self.assertEqual([1, 2, 3, 4], client.read_holding_registers(0x7000, 4))
            client.execute_reset(0)
        finally:
            client.close()
            thread.join(4)
            server.close()
        self.assertFalse(errors)
        self.assertEqual([(0, b"\x03\x70\x00\x00\x04"), (0, b"\x06\x00\x00\x00\x00")], requests)


if __name__ == "__main__":
    unittest.main()
