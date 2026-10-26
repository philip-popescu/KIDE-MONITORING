# KIDE Monitoring - Application Specification

Status: Input zone profile, unlatched behavior, circuit fault colors, and the
user-requested assumed output range are implemented. Actual relay feedback and
specific live circuit diagnoses remain unverified. Execute Reset, CustomTkinter,
and pymodbus are required.

Updated: 2026-10-26

## 1. Purpose and scope

Build a Python desktop GUI that connects to one Kidde 2X-A control panel over
Modbus TCP and displays the live states of four connected Kidde KE-IO3144 modules.
Show inputs in the main window and outputs in a separate window. Provide editable
panel IP and TCP port fields and a button to send documented Execute Reset.

### Confirmed requirements

| ID | Requirement |
| --- | --- |
| R01 | Communicate with a Kidde 2X-A using Modbus TCP. |
| R02 | Monitor four KE-IO3144 modules, all on loop 1, at addresses 1, 2, 3, and 4 respectively. |
| R03 | Display four input squares per module, for 16 input indicators total. |
| R04 | Arrange modules as Module 1 at row 1 / column 1, Module 2 at row 2 / column 1, Module 3 at row 1 / column 2, and Module 4 at row 2 / column 2. |
| R05 | Display output states in a separate window using the same module arrangement. |
| R06 | Inputs use dedicated unlatched zone alarm states. Verified output sources show active/inactive; the requested assumed output range shows the selected bit as set/clear with an explicit assumed label. |
| R07 | Provide editable panel IP address and TCP port fields. |
| R08 | Provide Reset panel using documented Execute Reset. The implementation-stage instruction supersedes the earlier electronics reboot requirement. |
| R09 | Input short circuit is yellow, open circuit is blue, and improper resistor value is purple. Only verified diagnostic values can establish a specific fault type. |
| R10 | Use panel ID 001, InitPan 1, Modbus Zonepoint, and sequential input zones 1-16: Module 1 uses 1-4, Module 2 uses 5-8, Module 3 uses 9-12, Module 4 uses 13-16. |
| R11 | Default the 16 output slots to references 0x7001-0x7010 in module/channel order, using device alarm bit 1 as an assumed display. Keep this source unverified and clearly labeled. |

### Proposed defaults

These are implementation choices, not additional confirmed user requirements:

- Target Windows initially; use English UI labels.
- Use four output squares per module, matching the requested input configuration
  and the request to use the same configuration for outputs.
- Within each module, arrange channel squares horizontally in order 1 through 4.
- Use labels `Active` and `Inactive` alongside colors to describe live I/O clearly.
- Default TCP port to `502`; leave the IP address blank until configured.
- Use CustomTkinter and pymodbus, as explicitly requested during implementation.
  Pin compatible dependency versions.
- Store connection settings and verified mappings in a local JSON configuration.

## 2. Sources and decisions

| Source | Information used |
| --- | --- |
| [DESCRIPTION.md](DESCRIPTION.md) | Original application request, colors, module grid, separate outputs window, connection fields, and restart action. |
| User clarifications, 2026-10-23 | Modules are on loop 1 at addresses 1-4; colors represent live I/O. During implementation the user authorized Execute Reset, required CustomTkinter and pymodbus, and requested removal of unneeded code. |
| User confirmations and PCC-EX screenshots, 2026-10-26 | Panel 001, Modbus Zonepoint, InitPan 1, sequential dedicated input zones 1-16, unlatched inputs, and yellow/blue/purple circuit fault colors. Output groups 1, 3, and 301 establish membership only. |
| User output-range instruction, 2026-10-26 | Use device-status references 0x7001-0x7010 as the default output mapping. This authorizes the assumed display without establishing relay feedback semantics. |
| [Modbus protocol guide](modbus_protocol_guide_for_addressable_control_panels_en.pdf) | Revision 004, issued 04JUN25. Printed pp. 3, 5-6, 16-20, 31-34 and Examples establish port, addressing, status flags, reset semantics, request size, and polling limits. |
| [2X-A installation manual](2x-a_series_installation_manual_ro.pdf) | Printed pp. 71, 98, and 100 describe Modbus activation, PAK, Zonepoint mode, and InitPan. |

The guide documents device and zone status flags. User-confirmed dedicated zones
now provide individual input alarm sources. Its zone fault bit does not identify
short circuit, open circuit, or improper resistance. Independent output status
and detailed circuit diagnoses remain unverified. Present Execute Reset as panel
reset, with no electronics reboot claim.

## 3. Screens and interaction

### 3.1 Main window: Inputs

Window title: `KIDE Monitoring - Inputs`.

Provide a connection area with panel IP, port, `Connect`, `Disconnect`, an
`Outputs` button, and a `Reset panel` button. Show connection status, the latest
communication error when present, and the last successful update time.

The module grid must be:

```text
                    Column 1                        Column 2
Row 1     Module 1 - Loop 1, address 1     Module 3 - Loop 1, address 3
                 IN1 IN2 IN3 IN4                  IN1 IN2 IN3 IN4

Row 2     Module 2 - Loop 1, address 2     Module 4 - Loop 1, address 4
                 IN1 IN2 IN3 IN4                  IN1 IN2 IN3 IN4
```

Each module card shows its name, loop/address, four equally sized square
indicators, channel labels, and readable state text. A shared legend explains
the colors. Resizing must preserve the module order and keep indicators legible.

### 3.2 Separate window: Outputs

Window title: `KIDE Monitoring - Outputs`.

Use the same grid and module labels as the main window, with `OUT1` through
`OUT4`. This is a separate top-level window that can remain visible alongside
the inputs window. It shares the main window's connection and data model.

- Opening the window repeatedly raises the existing window.
- Closing it keeps input monitoring running.
- Reopening it displays the latest available state immediately.
- Output squares display status; clicking them does not switch physical outputs.
- Closing the main window stops communication and closes the outputs window.

### 3.3 Indicator behavior

| State | Presentation |
| --- | --- |
| Fresh input zone alarm / verified output active | Red square and `Alarm` / `Active` text. |
| Fresh input zone normal / verified output inactive | Green square and `Normal` / `Inactive` text. |
| Selected bit set/clear in the assumed output range | Red/green square labeled `Set (assumed)` / `Clear (assumed)`, with its source register visible. Never label these as verified relay states. |
| Verified input short circuit | Yellow square and `Short circuit` text. |
| Verified input open circuit | Blue square and `Open circuit` text. |
| Verified improper input resistor value | Purple square and `Resistor fault` text. |
| General fault without a fresh, verified circuit diagnosis | Gray square and `Fault` text. |
| Zone in test, disabled, or prealarm | Gray square with the corresponding status text. |
| No verified mapping or no successful reading yet | Gray square and `Unknown` or `Not configured` text. |
| Reading is stale or unavailable | Gray square and `Stale` or `Unavailable` text; retain the last known value and timestamp as details only. |
| Device fault, test, or disabled condition is reported | Show a distinct badge/message. These flags do not themselves determine live I/O activity. |

Only a valid channel-level reading can establish an active or inactive color.
A successful TCP connection, zero-filled fallback, module alarm flag, or cached
value must not be interpreted as a fresh channel reading.

Where a module-level condition makes channel validity uncertain, show the
affected channels as unavailable. Preserve any separately verified live state
only when the mapping documentation establishes that it remains meaningful.

## 4. Device and channel mapping

### 4.1 Physical module inventory

| Display name | Grid position | Loop | Device address |
| --- | --- | --- | --- |
| Module 1 | Row 1, column 1 | 1 | 1 |
| Module 2 | Row 2, column 1 | 1 | 2 |
| Module 3 | Row 1, column 2 | 1 | 3 |
| Module 4 | Row 2, column 2 | 1 | 4 |

The confirmed panel ID is 001, BMS InitPan is 1, and the protocol is Modbus in
Zonepoint mode. The Modbus TCP unit identifier remains separately configurable.

### 4.2 Required channel mapping

Before commissioning, obtain a verified source for each of the 16 inputs and
16 outputs. Each configured channel must identify:

- Module, direction, channel number, and display label.
- Documented register reference and its zero-based Modbus PDU address.
- The verified extraction rule, such as bit mask or value comparison, including
  any inversion required to interpret active versus inactive.
- Whether the value describes a zone alarm, live input, commanded output, or
  measured output feedback, and whether it latches. The user specified unlatched
  input behavior; the application must not hold an alarm or fault after it clears.
- Source documentation or panel configuration reference used to verify it.

The description requests live output state. The implementation must identify
whether the available value is the panel's output activation state or physical
feedback. If only commanded state is exposed, document that limitation and obtain
agreement before treating it as the requested output indication.

Do not assume that a module address identifies its four channels independently,
that consecutive channels occupy consecutive registers, or that a module's alarm
bit can be copied into all four squares.

### 4.3 Confirmed input map and remaining source limitations

Input zones 1-16 use references `0x3001`-`0x3010`, or transmitted addresses
`0x3000`-`0x300F`. Within each module, channels I1-I4 occupy the next four zones.
The alarm mask is `0x0002`; fault is `0x0004`, test `0x0008`, and disabled `0x0010`.
Ship this profile for new configurations and provide Settings > Use zones 1-16 for
existing installations without overwriting connection endpoints or output maps.

Input fault rules may use independently verified holding-register sources, masks,
fault/normal codes, and documentation references. Include these reads in the same
four-register, one-second request schedule. Missing, stale, conflicting, or unknown
diagnoses must not be invented as yellow/blue/purple fault types. A general zone
fault stays gray when its detailed type is unavailable. Fault takes priority over
alarm; test/disabled conditions and stale data take precedence over colored data.
When the fresh zone no longer reports a fault, remove any diagnostic fault color.

In Zone/Point mode, the guide assigns one general status register per addressed
device. Its documented flags are prealarm (bit 0), alarm (bit 1), fault (bit 2),
test (bit 3), and disabled (bit 4). These flags do not establish the individual
live I/O states requested by the user.

For the specific example of the monitored panel occupying map slot NODE1 and
loop 1, device addresses 1-4 correspond to documented references
`0x7001`-`0x7004`, sent as PDU addresses `0x7000`-`0x7003`. This is an example
of module summary addressing, not a completed channel map. Recalculate the
mapping from the actual panel node ID and InitPan before using it.

PCC-EX confirms output membership: Module 1 O2 belongs to group 1; Module 1
O1/O3/O4 and Module 2 O1-O4 belong to group 3; Modules 3 and 4 O1-O4 belong to
group 301. These group numbers are not individual output status register addresses.

The user subsequently requested default output references `0x7001`-`0x7010`.
Assign four consecutive references per module, O1-O4 in order. Read them at PDU
addresses `0x7000`-`0x700F`, using the standard device alarm mask `0x0002` as the
initial selection. Preserve the source kind `assumed_device_status` and
`verified: false`. Show the register on each tile, assumed labels, an output
notice, and an assumed qualifier in the footer. General device faults/test/
disabled/prealarm states remain gray. Each slot uses its own selected source;
do not gate a slot using an unrelated physical module's summary word.

New installation profiles include this assumed range. Settings > Use output
range applies it explicitly to existing configurations, preserving inputs and
connection/reset settings. Applying the input zone profile to existing settings
preserves configured output mappings. Read planning deduplicates any shared
device-summary references and retains the four-register/request limit.

Simulation must demonstrate normal, alarm, short circuit, open circuit, and
resistor fault independently, using synthetic diagnostic data kept out of saved
live configurations. The Input selector can force each state for visual checks.

## 5. Modbus communication

### 5.1 Prerequisites

Confirm that the panel has Modbus over Ethernet enabled and the appropriate PAK
registered. The supplied installation manual lists `2010-2-PAK-RMMB` for Modbus.
Record the panel firmware, node ID, BMS mode, and InitPan in commissioning notes.

Device status addressing requires Zonepoint mode; the guide's Zone mode map
provides zone status instead. Zone information alone is insufficient unless a
verified panel configuration establishes the requested independent live states.

### 5.2 Protocol rules

- Use Modbus TCP. Default port: `502`, editable by the user.
- Use function `03` for documented holding-register status reads.
- Read at most **four registers per request**.
- Leave at least **1,000 ms between successive requests** sent to the panel.
  Apply this across both windows, retries, and any supported command writes.
- Serialize requests through one shared connection worker. Opening the outputs
  window must not create a second polling stream.
- Convert documented one-based register references to zero-based PDU addresses
  exactly once. Keep both representations explicit in mappings and diagnostics.
- Make the Modbus unit identifier configurable. The guide's examples use `0`;
  validate the required value against the installation.
- Validate response function, size, register count, and Modbus exceptions before
  updating a channel. An exception is not an inactive state.

### 5.3 Refresh, reconnect, and stale data

Create a read schedule from the verified mappings, combining contiguous reads
where possible without exceeding four registers. Both windows consume its results.

The refresh period depends on the number of requests. With N requests per full
scan, the best repeated scan period is approximately N seconds, before additional
timeouts or delays. For example, eight requests imply approximately an eight-second
scan, not a one-second refresh of every channel. Show actual update timestamps.

Proposed defaults:

- Network request timeout: 3 seconds.
- Retry delay after communication failure: 2 seconds, increasing to a maximum
  of 30 seconds; the protocol request spacing remains mandatory.
- Per-channel stale threshold: the greater of 5 seconds or twice its planned
  refresh period. Recalculate this threshold if the read schedule changes.

Track freshness per source/channel so one successful read does not make unrelated
values fresh. Mark affected data unavailable on communication failure and stale
when its freshness threshold expires. Reconnect automatically after unexpected
connection loss; an explicit `Disconnect` stops reconnection until `Connect`.

## 6. Execute Reset action

The user authorized documented Execute Reset during implementation. Send function
`06` to reference `0x0001` / PDU address `0x0000`. The entire 16-bit value is the
target panel ID: `0` for local or `1`-`128` for a specific panel. Default to local.
Broadcast reset (`0xFFFF`) is excluded.

1. Label the button `Reset panel`; enable it only in a connected live session
   when reset is enabled in configuration.
2. Show a confirmation dialog identifying the endpoint and target.
3. Submit exactly one command through the shared request scheduler.
4. Validate the response echo. Distinguish acknowledged, rejected, and unknown
   outcomes. A dropped connection does not establish success.
5. Invalidate cached monitoring data and reconnect after acknowledgement or an
   uncertain outcome, then resume monitoring using fresh readings.
6. Do not automatically retry, replay on reconnect, or broadcast the command.
7. Keep the action disabled in simulation mode.

## 7. Configuration, implementation, and diagnostics

- Keep the GUI event loop responsive; perform network work outside it and pass
  results back using the selected toolkit's supported mechanism.
- Separate connection/scheduling, register decoding, channel state, and UI code
  so protocol mappings can be verified independently of rendering.
- Persist panel IP, port, unit identifier, node/InitPan information, module labels,
  reset target, and verified channel mappings. Validate configuration before connecting.
- Reject invalid IP addresses, ports outside `1`-`65535`, and malformed mappings
  with a clear message. Missing mappings leave the affected channels unknown.
- Load saved settings on startup. Proposed default: connect only when the user
  presses `Connect`; do not send a reset during startup or configuration loading.
- Log connection changes, mapping errors, timeouts, Modbus exceptions, and reset
  attempts/outcomes to a bounded rotating local log with timestamps.
- Provide a clearly labeled simulation mode for UI and failure testing without
  panel access. Simulated values must never appear as live readings.

## 8. Acceptance criteria and verification

| ID | Acceptance criterion |
| --- | --- |
| A01 | The inputs window displays exactly four modules and 16 input squares in the specified grid, with the confirmed loop/address labels. |
| A02 | The separate outputs window displays four modules in the identical grid and, under the proposed four-output assumption, 16 output squares. |
| A03 | Verified normal/alarm and short/open/resistor states use green/red/yellow/blue/purple with readable labels. Per-channel fault codes change only the matching input. Faults remain gray without a verified specific diagnosis. |
| A04 | Missing mappings, malformed responses, device unavailability, startup without data, and connection failures never produce fabricated green states. |
| A05 | Both windows share one connection and request schedule; repeated opens and window closes behave as specified. |
| A06 | IP and port validation works, saved settings reload, and the GUI remains responsive during timeouts and reconnection. |
| A07 | Protocol-level verification confirms correct zero-based addressing, a maximum of four registers per read, and at least 1,000 ms between all requests. |
| A08 | Channel-level timestamps and stale/unavailable behavior remain correct during partial read failures and longer scan cycles. |
| A09 | A verified mapping identifies every live input and output independently. Commissioning confirms every indicator against a known channel state and documents output-state semantics. |
| A10 | Reset uses function 06 / PDU address 0 and the selected local or explicit target, sends one command after UI confirmation, handles uncertain outcomes, and resumes monitoring afterward. |
| A11 | Confirmed input zone mappings preserve unlatched transitions. A cleared alarm/fault returns to the fresh reported state immediately; no automatic panel reset or software latch is added. |
| A12 | The default output range round-trips as unverified assumed status, reads all 16 references in batches of four, shows set/clear rather than relay activity, and retains freshness/error handling per source. |

Use a Modbus simulator/fake transport for decoding, scheduling, exception, timeout,
and command-outcome checks. Perform a visual review of both windows. Hardware
commissioning is a separate validation step after the protocol dependencies are
resolved; this specification does not claim that those checks have been performed.

## 9. Open items before implementation can be completed

| Item | Required information or decision | Current status |
| --- | --- | --- |
| O01 | Individual input and output read sources. | Input zone alarm mapping confirmed. User-requested output range implemented as an assumption; actual relay-state semantics remain unverified. |
| O02 | Panel action. | Resolved: user authorized Execute Reset; implemented as Reset panel. |
| O03 | Panel/BMS settings and Modbus unit identifier. | Panel 001, InitPan 1, Modbus Zonepoint confirmed in PCC-EX. Unit identifier is separately configurable; default 0. |
| O04 | Whether output state means panel activation state or physical feedback, based on what the panel exposes. | Resolve alongside O01. |
| O05 | Windows target, English labels, four outputs per module, and horizontal channel arrangement. | Proposed defaults, open to revision. |
| O06 | Separate short/open/improper-resistor diagnosis codes. | Not exposed by the supplied standard zone map. UI, simulation, and configurable verified rules are implemented; live subtype detection needs an additional documented source. |

## 10. Initial delivery scope

Once the protocol dependencies are resolved, deliver the Python application,
dependency specification, configuration template, setup/run instructions,
simulation mode, and relevant verification results. Standalone executable
packaging is optional and can be agreed later.

The initial scope excludes output switching, arm/disarm commands, alarm
acknowledgement, sounder controls, dashboards for multiple installations, remote
access, and long-term event reporting. The only requested panel action is the
Execute Reset action defined above.
