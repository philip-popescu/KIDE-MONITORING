# KIDE Monitoring

A CustomTkinter desktop app for a Kidde 2X-A panel, using pymodbus over Modbus TCP.
Modules 1-4 default to loop 1, addresses 1-4. The inputs and outputs windows share
one connection. The inputs use the confirmed, dedicated zones 1-16 on panel 001,
with InitPan 1 and Zonepoint mode. Inputs are treated as unlatched: the app follows
each fresh reported state without holding an alarm after it clears.

| Input color | State |
| --- | --- |
| Green | Normal / inactive |
| Red | Alarm / active |
| Yellow | Verified short circuit |
| Blue | Verified open circuit |
| Purple | Verified improper resistor value (`Resistor fault`) |
| Gray | Unknown, unavailable, stale, test, disabled, prealarm, or an unspecified fault |

The documented zone map reports a general fault bit. It does not distinguish
short circuit, open circuit, or improper resistance. Live zone faults therefore
show **Fault** in gray until a verified diagnostic source identifies their type.
The app does not infer a circuit type from alarm/fault bits or reserved bits.

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
readings, create a connection outage, or report a module fault. The **Input**
selector demonstrates Normal, Alarm, Short circuit, Open circuit, and Resistor
fault with synthetic signals. Simulation never
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

New installations use the user's confirmed input-zone mapping:

| Module | Inputs I1-I4 use zones | Register references |
| --- | --- | --- |
| 1 | 1-4 | `0x3001`-`0x3004` |
| 2 | 5-8 | `0x3005`-`0x3008` |
| 3 | 9-12 | `0x3009`-`0x300C` |
| 4 | 13-16 | `0x300D`-`0x3010` |

These are panel-reported zone alarm states, with alarm mask `0x0002`, fault
`0x0004`, test `0x0008`, and disabled `0x0010`. Transmitted addresses are one less
than the references. No software latch is applied.

Existing saved files remain intact. To apply this profile, disconnect and choose
**Settings > Use zones 1-16**. It saves the input mappings, panel ID 1, InitPan 1,
and Zonepoint mode while preserving the IP/port and output mappings.

The default output range is the user-requested `0x7001`-`0x7010`, assigned in
module/channel order:

| Module | Outputs O1-O4 use register references |
| --- | --- |
| 1 | `0x7001`-`0x7004` |
| 2 | `0x7005`-`0x7008` |
| 3 | `0x7009`-`0x700C` |
| 4 | `0x700D`-`0x7010` |

Transmitted addresses are `0x7000`-`0x700F`. The initial display selects bit 1,
mask `0x0002`, which is the documented device alarm bit. This is an **assumed
output mapping**: these words report devices, and their use as individual relay
states is unverified. The output tiles therefore say **Set (assumed)** or
**Clear (assumed)**, show their register references, and keep `verified: false`.
Device fault, test, disabled, prealarm, and unavailable readings stay gray.

For an existing saved file, disconnect and select **Settings > Use output range**.
This explicitly applies the range to all 16 outputs while preserving input maps,
connection settings, and reset settings. New profiles use this range by default.
Existing custom output sources are preserved when applying input zones.

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
| `data_kind` | `zone_alarm`, `live_input`, `physical_output_feedback`, `output_activation`, or output-only `assumed_device_status`. |
| `verified` | Live I/O mappings require `true`. The explicit `assumed_device_status` output mapping requires `false` and is displayed as assumed status. |
| `latched` | The confirmed inputs use `false`. A `zone_alarm` source may explicitly record latched/unknown behavior, but a live I/O source requires `false`. |
| `source` | Required reference explaining how the channel source was verified. |
| `output_activation_accepted` | Must be `true` when using commanded output activation rather than measured feedback. |
| `valid_during_module_condition` | Defaults to `false`. Set true only if the source remains valid during module fault, test, disabled, or unavailable-summary conditions. |
| `fault_rules` | Optional verified diagnostic rules for input circuit faults; defaults to an empty list. |

Each item in `fault_rules` identifies a diagnostic `register_reference`, `mask`,
fault `value`, `normal_value` (default 0), `state`, `verified: true`, and a nonempty
documentation `source`. Supported states are `short_circuit`, `open_circuit`, and
`resistor_fault`. Fault and normal codes must differ and fit the mask. Rules
sharing a mask must agree on its normal code. Duplicate or unverified rules are
rejected; unexpected codes and conflicting diagnoses stay gray.

For zone inputs, detailed diagnosis is shown only while the zone reports a fault.
The diagnosis must be fresh independently of the zone reading. A missing/stale
diagnosis leaves an unspecified gray fault, and a cleared zone fault immediately
removes its fault color. No diagnostic register addresses are supplied for the
real panel because those sources are not established in the provided guide.
Do not copy synthetic diagnostic addresses from simulation into a live mapping.
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
conditions, all three circuit diagnoses, unlatched recovery, ambiguous/unknown
faults, configuration validation, request limits/timing, and one-shot reset.
A loopback TCP test checks actual pymodbus read/reset frames against a local
test responder. The GUI smoke check opens both windows using synthetic values;
it does not contact a panel. Hardware commissioning still requires verified
verified relay/diagnostic mappings and comparison against the real installation.
The default output range is an assumption and is not hardware commissioning evidence.

Architecture: `config.py` validates/persists settings, `state.py` plans reads and
derives indicator states, `modbus.py` applies panel constraints to pymodbus,
`engine.py` serializes communication, and `ui.py` renders the shared state.

Library references: [CustomTkinter](https://customtkinter.tomschimansky.com/documentation/)
and [pymodbus client documentation](https://pymodbus.readthedocs.io/en/v3.16.1/source/client.html).
