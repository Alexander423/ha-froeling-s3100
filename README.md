# Fröling Lambdatronic S3100 for Home Assistant

[![CI](https://github.com/Alexander423/ha-froeling-s3100/actions/workflows/ci.yml/badge.svg)](https://github.com/Alexander423/ha-froeling-s3100/actions/workflows/ci.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Native Home Assistant integration for wood and pellet boilers with the
**Fröling Lambdatronic S3100** controller (e.g. FHG Turbo 3000), plus the
standalone asynchronous Python library `froeling-s3100` it is built on.

The S3100 predates Modbus. It speaks a proprietary serial protocol on its
service connector ("Bedien-Erweiterung"). This project implements that
protocol based on the reverse engineering in
[dhoepfl/Radiator](https://github.com/dhoepfl/Radiator), extended with
captures from a real controller. Integrations for the Lambdatronic 3200
(Modbus) do **not** work with the S3100.

## Features

- **Local push.** The controller sends all values once per second. Nothing is polled.
- **Dynamic discovery.** Entities are created from what *your* controller announces: measurements, status texts, operating modes and the customer menu with its parameters.
- **Read-only by default.** Optionally, parameters of the customer menu can be changed: target temperatures, heating curves, heating times and similar. Writes are validated against the limits announced by the controller and confirmed by it.
- **Faults.**
  - The "Fault" problem sensor, the "Last fault" sensors and a fault event entity cover live faults.
  - The error history is included in the diagnostics.
- **Diagnostics.** The download contains:
  - link statistics (frames, latency, timeouts, reconnects)
  - the complete decoded catalog with raw bytes
  - unknown frames, for further reverse engineering

  The bridge address is redacted.
- **Robust connection.**
  - Reconnects automatically with exponential backoff.
  - A watchdog detects a silent controller.
  - Garbled bytes are resynchronised.
  - The catalog is cached, so entities are available right after a Home Assistant restart.

## Hardware

```
S3100 "Bedien-Erweiterung"            RS232/Ethernet bridge (DB9)
  TXD  ─────────────────────────────►  pin 2 (RXD)
  RXD  ◄─────────────────────────────  pin 3 (TXD)
  GND  ──────────────────────────────  pin 5 (GND)
  +US  not connected
```

Measure TXD against GND before connecting. You should see −5 to −12 V (RS232
levels). If you measure +3.3/+5 V, the port uses TTL levels and you need a
MAX3232 level shifter.

Bridge settings (tested with a Waveshare RS232/485/422 TO POE ETH (B)):

| Setting | Value |
|---|---|
| Work mode | TCP Server, port 4196 |
| Serial | 9600 baud, 8 data bits, no parity, 1 stop bit, no flow control |
| Protocol | None (transparent): no Modbus gateway, no MQTT, no registration/heartbeat packets |

Only one client may talk to the controller at a time. While Home Assistant is
connected, do not connect other tools to the bridge.

## Installation

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/Alexander423/ha-froeling-s3100` with type *Integration*.
2. Install **Fröling Lambdatronic S3100** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Fröling Lambdatronic S3100*.
4. Enter the IP address and port of the bridge.

Setup checks that the controller answers. The first start takes about 30 seconds,
because the controller transmits its whole configuration at 9600 baud.

### Options

| Option | Default | Description |
|---|---|---|
| Update interval | 10 s | How often states are written to Home Assistant. The controller sends values every second. |
| Allow changing parameters | off | Exposes customer-menu parameters as `number`, `time` and `switch` entities. While enabled, a repair notice reminds you. |

### Removal

Remove the integration under *Settings → Devices & services*. The cached
configuration is deleted with it.

## Entities

| Entity | Notes |
|---|---|
| Measurements | Everything in the cyclic telegram: temperatures, fan, air flaps, O2, buffer, hot water, heating circuits, operating hours … |
| Status / Operating mode | Status line of the display (e.g. *Heizen*, *Feuer-Aus*) and operating mode (*Winterbetrieb*, *Sommerbetrieb* …) as enum sensors |
| Fault (binary sensor) | On while the display shows *Störung* |
| Fire active / Heating up | Derived from the status line (*Anheizen*, *Heizen*, *Feuerhaltung*, *Vorwärmphase*, *Zünden*). *Heating up* is a handy automation trigger. |
| Heating circuit N demand | **Derived.** On while the controller's flow temperature target of the circuit is above 0 °C. The S3100 does not transmit the pump relays themselves, so this approximates "circuit is heating". |
| Buffer pump / induced draft fan running | **Derived.** On while the respective output is above 0 %. |
| Fault message (event) | Fires on every live fault with error id and text |
| Last fault / time | From live faults or the error history |
| Parameters | Customer-menu parameters, read-only by default (diagnostic) |
| Connection | Diagnostic connectivity sensor |

Service and combustion parameters are never exposed as entities. You can
inspect them in the diagnostics.

### Example automation

```yaml
automation:
  - alias: "Notify on boiler fault"
    triggers:
      - trigger: state
        entity_id: event.froling_s3100_fault_message
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "Boiler: {{ trigger.to_state.attributes.text }}"
```

## Known limitations

- Writing parameters (`RI`) is implemented with strict validation but has
  not been verified on every controller firmware. It is off by default.
- The meaning of some configuration blocks (weekly programs `MG`, `MK`,
  product table `ML`, `MS`) is not fully decoded. Their raw content is
  available in the diagnostics.
- Entity names of parameters come from the controller menu and are in
  German.
- Only the values the controller lists in its MA table are transmitted.
  On the tested controller that excludes the heating circuit pumps and
  mixers, although the controller knows further values (visible as
  `unused_formats` in the diagnostics). Air flaps and other outputs cannot
  be controlled: the protocol has no command for it and the combustion
  control must stay with the controller.
- Climate entities are not provided, because the S3100 has no clear
  per-circuit target/mode model that maps to a thermostat.

## Troubleshooting

| Symptom | Check |
|---|---|
| *Cannot reach the bridge* | IP address and port of the bridge, switch VLAN/PVID, PoE power |
| *S3100 does not answer* | TX/RX crossed? GND connected? 9600 8N1? Boiler switched on? |
| Frequent reconnects | Diagnostics → `statistics`: `checksum_errors`, `timeouts`. Shorten or shield the RS232 cable. |

## Library

The protocol library lives in `src/froeling_s3100` and has no Home Assistant
dependency. While the integration is distributed through HACS, a copy of it
is bundled in `custom_components/froeling_s3100/s3100`. Run
`python tools/sync_library.py` after changing the library. CI checks that
both are identical.

```python
from froeling_s3100 import S3100Client

client = S3100Client("192.168.178.60")
await client.start()
catalog = await client.wait_ready(120)
print(client.values)
```

`python -m froeling_s3100.simulator capture.jsonl` runs a simulated
controller from a capture made with `tools/s3100_capture.py`.

## License

MIT. Protocol knowledge courtesy of Daniel Höpfl's Radiator project (MIT).
