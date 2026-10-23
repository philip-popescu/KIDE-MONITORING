"""Panel constraints and response validation around the pymodbus TCP client."""

from pymodbus.client import ModbusTcpClient as PymodbusTcpClient
from pymodbus.exceptions import ModbusException as LibraryError


class ProtocolError(OSError):
    pass


class ModbusException(Exception):
    def __init__(self, code):
        self.code = code
        names = {1: "Illegal function", 2: "Illegal data address", 3: "Illegal data value",
                 4: "Server device failure", 5: "Processing", 6: "Server busy",
                 8: "Memory parity error", 10: "Gateway unavailable", 11: "Gateway timeout"}
        super().__init__(f"Modbus exception {code}: {names.get(code, 'Unknown exception')}")


class PanelClient:
    def __init__(self, host, port=502, unit_id=0, timeout=3.0):
        self.unit_id = unit_id
        # Retransmission belongs to our shared, rate-limited worker.
        # A lost reset acknowledgement must never resend the write.
        self.client = PymodbusTcpClient(host, port=port, timeout=timeout, retries=0)

    def connect(self):
        try:
            if not self.client.connect():
                raise OSError("Cannot connect to the panel. Check its IP address and port.")
        except LibraryError as exc:
            raise ProtocolError(str(exc)) from exc

    def close(self):
        self.client.close()

    def _request(self, function, **kwargs):
        if not self.client.connected:
            raise ProtocolError("Panel connection is closed.")
        try:
            response = function(device_id=self.unit_id, **kwargs)
        except LibraryError as exc:
            raise ProtocolError(str(exc)) from exc
        if response is None:
            raise ProtocolError("No response received.")
        if response.isError():
            code = getattr(response, "exception_code", None)
            if code is not None:
                raise ModbusException(code)
            raise ProtocolError(str(response))
        if response.dev_id != self.unit_id:
            raise ProtocolError("Response unit identifier does not match.")
        return response

    def read_holding_registers(self, address, count):
        if not 1 <= count <= 4 or not 0 <= address <= 65536 - count:
            raise ValueError("Panel reads require 1-4 registers within the PDU address range.")
        response = self._request(self.client.read_holding_registers, address=address, count=count)
        values = response.registers
        if response.function_code != 3 or len(values) != count or any(type(v) is not int or not 0 <= v <= 65535 for v in values):
            raise ProtocolError("Response function or register data does not match the read request.")
        return list(values)

    def execute_reset(self, target_panel_id):
        if type(target_panel_id) is not int or not 0 <= target_panel_id <= 128:
            raise ValueError("Reset targets must be 0 (local panel) or a panel ID from 1 to 128.")
        # Documented reference 0x0001 is PDU address 0. Value is the entire panel ID.
        response = self._request(self.client.write_register, address=0, value=target_panel_id)
        if response.function_code != 6 or response.address != 0 or list(response.registers) != [target_panel_id]:
            raise ProtocolError("Reset acknowledgement does not echo the command; outcome is unknown.")
