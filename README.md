# KIDE Monitoring

A CustomTkinter desktop app for a Kidde 2X-A panel, using pymodbus over Modbus TCP.
Modules 1-4 default to loop 1, addresses 1-4. The inputs and outputs windows share
one connection. Red means active, green means inactive, and gray means unknown,
stale, or unavailable.

## Install and run

Use Python 3.10 or later with Tk support. In PowerShell, from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run.bat
```

The environment in this workspace is already installed. Double-click `run.bat`
to launch, or preview synthetic signals immediately:

```powershell
.\run.bat --simulate
```

Choose **Live Modbus**, enter the panel IP and port, and click **Connect**.
The default port is 502. Live connection settings are saved to `config.json`.
**Outputs** opens a separate window; closing it keeps monitoring running.
**Disconnect** also stops automatic reconnect attempts. Settings can be changed
while disconnected.

Simulation is prominently labeled in both windows. Its controls can pause
readings, create a connection outage, or report a module fault. Simulation never
connects to the panel, enables reset, or writes synthetic mappings into your live
configuration.

## Build a single executable

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe build_exe.py
```

The build script uses `--onefile` and `--windowed`, producing
`dist/KIDE_Monitoring.exe`. It includes CustomTkinter's fonts, themes, and icons
with `--collect-all customtkinter`. Python is not required on the target computer.
Build on Windows to produce the Windows executable.

For troubleshooting, add `--console` to the build command. The executable supports
the same `--simulate` and `--config` options as the source app. Its default
`config.json` and `logs` directory are stored beside the executable so they
persist between runs. Keep it in a directory where your user can write files.

Build options follow the [PyInstaller documentation](https://pyinstaller.org/en/stable/usage.html).

## Panel reset

The user authorized the documented **Execute Reset** command. **Reset panel**
asks for confirmation, then writes function 06 to PDU address 0 (documented
reference `0x0001`), with the target panel ID as the register value. Settings use
target `0` for the local panel by default; targets 1-128 can select a panel.
Broadcast targets are rejected. This is panel reset, without a claim that it
reboots the panel electronics.

Reset is available only while connected in live mode and enabled in Settings.
The command is sent once. Acknowledged, rejected, and unknown outcomes are
handled separately; an acknowledgement lost during a disconnect never causes
an automatic resend. Monitoring restarts with fresh data afterward.

## Live channel configuration

The supplied protocol guide documents module summary flags, but does not
establish independent live input/output registers for the KE-IO3144. The app
therefore ships with all 32 channel mappings unset. They remain gray until a
verified mapping is provided. The physical module addresses alone do not
establish their channel sources.

Copy `config.example.json` to `config.json`, or load a separate JSON file from
**Settings > Load configuration...**. The selected file becomes the active
configuration and receives saved live connection settings. You can also select
a file at launch:

```powershell
.\run.bat --config .\installation.json
.\.venv\Scripts\python.exe main.py --config .\installation.json --validate-config
```

In Settings, optionally set **both** the panel node ID and **BMS InitPan** to read
module summaries in Zonepoint mode. The module status register reports alarm,
fault, test, and disabled conditions; it does not supply the four channel colors.
Ensure Modbus is enabled on the panel with its appropriate activation key.

Each module contains four `inputs` and four `outputs`. Replace a channel's
`"mapping": null` with a verified object containing:

| Field | Meaning |
| --- | --- |
| `register_reference` | One-based register number, either integer or a hexadecimal string. |
| `pdu_address` | Optional cross-check: exactly `register_reference - 1`. |
| `mask` | Nonzero 16-bit mask selecting the documented channel value. |
| `active_value` / `inactive_value` | Distinct values after masking; swapping them inverts the interpretation. Any other masked value is unavailable. |
| `data_kind` | `live_input`, `physical_output_feedback`, or `output_activation`. |
| `verified` | Must be `true`, based on the installation's verified documentation/configuration. |
| `latched` | Must be `false`; latched alarms do not represent live I/O. |
| `source` | Required reference explaining how the channel source was verified. |
| `output_activation_accepted` | Must be `true` when using commanded output activation rather than measured feedback. |
| `valid_during_module_condition` | Defaults to `false`. Set true only if the source remains valid during module fault, test, disabled, or unavailable-summary conditions. |

No real channel register examples are provided because their addresses are not
yet established. Do not copy simulation addresses into a live configuration.
Click a square to inspect its source type, mapping, last value, and timestamp.

## Refresh and diagnostics

Reads are limited to four registers, with at least one second between all Modbus
requests, including resets. The footer shows the resulting full-scan interval.
Each source has independent timestamps and freshness. A bad response never turns
a square green. Module fault/test/disabled flags invalidate related channels
unless their documented mapping explicitly establishes continued validity.

Unexpected disconnects reconnect with bounded backoff. Manual disconnect stops
retries. Logs rotate in `logs/monitor.log` (three backups, 1 MB per file).

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\gui_smoke.py
```

Tests cover independent channel decoding, partial failures, stale data, module
conditions, configuration validation, request limits/timing, and one-shot reset.
A loopback TCP test checks actual pymodbus read/reset frames against a local
test responder. The GUI smoke check opens both windows using synthetic values;
it does not contact a panel. Hardware commissioning still requires verified
channel mappings and comparison against the real installation.

Architecture: `config.py` validates/persists settings, `state.py` plans reads and
derives indicator states, `modbus.py` applies panel constraints to pymodbus,
`engine.py` serializes communication, and `ui.py` renders the shared state.

Library references: [CustomTkinter](https://customtkinter.tomschimansky.com/documentation/)
and [pymodbus client documentation](https://pymodbus.readthedocs.io/en/v3.16.1/source/client.html).
