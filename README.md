# Arduino Robot Steering — PC ↔ Arduino Interface

A small, pragmatic serial protocol and Python tooling to steer an Arduino-based mobile robot from a PC. It includes:

* A robust **framed request/response protocol** with sequence numbers, XOR checksum, ACK/NACK, retries, and exponential backoff. 
* A **RobotClient** library for Python that implements the protocol and reconnection logic. 
* An **interactive CLI** with command aliases, REPL, batch mode, history, and log export. 
* A **thread-safe communication logger** that can export JSON/CSV/TXT and render the log as a string at runtime. 

---

## Contents

* [Project Structure](#project-structure)
* [CLI Usage](#cli-usage)
* [Python API Quick Start](#python-api-quick-start)
* [Architecture & Layers](#architecture--layers)
* [Framing & Checksum](#framing--checksum)
* [Request/Response Lifecycle & States](#requestresponse-lifecycle--states)
* [Commands (CMD) & Examples](#commands-cmd--examples)
* [Security Description](#security-description)
* [Logging](#logging)
* [Arduino Firmware overview](#arduino-firmware-overview)
* [Pin mapping](#pin-mapping-wiring)
* [Motion model & calibration](#motion-model--calibration)
* [Firmware error codes](#firmware-error-codes-nack)
* [Errors & Limitations](#errors--limitations)




---


## Project Structure

* `robot_client.py` — protocol, framing, retries/backoff, reconnect, helpers. 
* `CLI.py` — interactive shell & batch runner over `RobotClient`. 
* `log_manager.py` — thread-safe logger with TXT/CSV/NDJSON export and `to_string()`. 
* `arduino.ino` — firmware handling framed serial commands (PING, V, M, R, S, B, I, STATUS), motor control, and sensor reads over 9600 baud. 

---

## CLI Usage

The CLI is a friendly shell over `RobotClient`:

```
python CLI.py --port COM6
python CLI.py --port /dev/ttyUSB0 --baud 115200
python CLI.py --port COM7 "PING" "V:160" "M:20" "B" "STATUS"
python CLI.py --port COM7 --script cmds.txt
```

Interactive commands / aliases:

* `ping`, `status`, `v <0..255>`, `m <cm>`, `r <deg>`, `s`, `b`, `i`
* `history` — print in-memory log
* `save-log <path>` — write logs as `.txt`, `.csv`, or NDJSON `.json`
* `reconnect [--port P] [--baud B]` — reopen with health check
* `quit` / `exit` (or press **Ctrl-D**)

Raw tokens like `V:160` or `R:-90` also work. 

---

## Python API Quick Start

```py
from robot_client import RobotClient

rc = RobotClient("COM6", 9600, max_retries=5)
print(rc.ping())         # -> "OK" (example)
print(rc.set_v(160))     # -> confirmation
print(rc.move_cm(25))    # -> confirmation/odometry
print(rc.sonar())        # -> "123" (cm)
print(rc.history())      # -> all logs as text
```

---

## Architecture & Layers

### 1) Application Layer (Robot actions)

High-level robot operations exposed as commands (`PING`, `STATUS`, `V`, `M`, `R`, `S`, `B`, `I`). On the PC, these are one-liners like `rc.move_cm(20)` or `rc.sonar()`. The CLI wraps the same calls with an ergonomic REPL and batch-file support.  

### 2) Transport/Protocol Layer (Framed serial)

Each message is sent as a **framed** byte stream with start/end markers, an **8-bit sequence number**, fields separated by `|`, and an **XOR checksum**. The client enforces **ACK/NACK semantics**, **retries with exponential backoff**, and **sequence matching**. 

### 3) I/O Layer (PySerial)

Raw serial I/O with read-until-frame semantics (`^ ... $`) and a guarded buffer to avoid runaway frames. Connection open/close and **reconnect with backoff** are included. 

---




## Framing & Checksum

**Frame format (both directions):**

```
^<SEQ>|<CMD>|<PAYLOAD>*<CS>$
```

* `^` / `$`: start/end markers
* `SEQ`: 1 byte, hex-encoded (`00`–`FF`), wraps at 255
* `CMD`: ASCII token (e.g., `PING`, `ACK`, `NACK`, `STATUS`, `V`, `M`, `R`, `S`, `B`, `I`)
* `PAYLOAD`: ASCII text (can be empty)
* `*`: checksum separator
* `CS`: XOR of all bytes between `^` and `*` (inclusive of `SEQ|CMD|PAYLOAD`), rendered as two hex digits

Client helpers: `xor_checksum`, `build_frame`, `parse_frame`. 

**Canonical response types:**

* Success: `^<SEQ>|ACK|<INFO>*<CS>$`
* Failure: `^<SEQ>|NACK|<ERROR>*<CS>$`
  (Where `<INFO>` and `<ERROR>` are application-defined text.) 

---

## Request/Response Lifecycle & States

**Lifecycle (client perspective):**

1. **Idle → Build:** allocate next `SEQ` (0x00–0xFF wrap), build frame. 
2. **Send:** write TX, log TX.  
3. **Wait:** read until a complete `^...$` frame or timeout; verify checksum; parse. Guard drops frames >256 bytes. 
4. **Match:** ignore frames whose `SEQ` ≠ request `SEQ`; continue waiting within the same attempt. 
5. **Handle:**

   * On `ACK`: return payload to caller (success). 
   * On `NACK BAD_CS`: immediately **resend** this request (shortcut path). 
   * On other `NACK`: raise error to caller. 
6. **Retry (on timeout or parse error):** double timeout (**exponential backoff**) and retry up to `max_retries`. Default `base_timeout=0.6`, `max_retries=3`. 
7. **Fail:** if no valid `ACK` after all attempts → Timeout error. 

**Link state machine:**

* **OPEN** (healthy) → used normally.
* **DEGRADED** (no response to health check) → **RECONNECTING**: close/reopen port with backoff, optional DTR toggle, then test `PING`. On success: **OPEN**; on repeated failure: **DOWN**. Managed by `reconnect_serial()` and `is_link_alive()`. 

---

## Commands (CMD) & Examples

### Summary table

| CMD       | Meaning                | Payload (request) | Response (ACK payload)              | Notes                                       |
| --------- | ---------------------- | ----------------- | ----------------------------------- | ------------------------------------------- |
| `PING`    | Health check           | empty             | implementation-defined (e.g., `OK`) | Used by `is_link_alive()` after reconnect.  |
| `STATUS`  | Robot status           | empty             | status text/json                    |                                             |
| `V`       | Set linear speed (PWM) | `0..255`          | confirmation / applied value        | CLI alias: `v 160`.                         |
| `M`       | Move centimeters       | signed integer    | confirmation / odometry             | `+` forward, `-` back.                      |
| `R`       | Rotate degrees         | signed integer    | confirmation / heading change       | `+` right, `-` left.                        |
| `S`       | Emergency stop         | empty             | confirmation                        |                                             |
| `B`       | Sonar read (cm)        | empty             | integer distance                    |                                             |
| `I`       | IR sensor read         | empty             | sensor value                        |                                             |
| `HELP`    | Help text              | empty             | textual help                        | For debugging.                              |
| `HISTORY` | Client-side only       | —                 | returns local log dump              | Provided by client/CLI.                     |

Client convenience wrappers: `ping()`, `status()`, `set_v(pwm)`, `move_cm(cm)`, `rotate_deg(deg)`, `stop()`, `sonar()`, `ir()`. 

### Frame examples (request side)

Examples below show **requests** the client would send (hex `SEQ` shown; checksums are computed):

```
^01|PING|*11$
^02|V|160*63$
^03|M|25*49$
^04|R|-90*72$
^05|B|*47$
^06|I|*4F$
^07|S|*54$
^08|STATUS|*1C$
```

The Arduino should respond with matching `SEQ` and either `ACK` (with info) or `NACK` (with error), e.g.:

```
^01|ACK|OK*??$         (OK to PING)
^03|ACK|OK*??$         (moved 25 cm)
^04|NACK|OUT_OF_RANGE*??$  (example error)
```

---


## Security Description

Current measures:

* **Framing + XOR checksum**: integrity against random line noise; detects malformed/corrupted frames. 
* **Sequence numbers**: correlates responses to requests; drops mismatched/out-of-order traffic. 
* **ACK/NACK semantics with retries/backoff**: increases robustness in lossy conditions; mitigates transient faults. 

What’s **not** provided:

* **Authentication, encryption, anti-replay**: XOR checksum is not a MAC; anyone with port access can inject frames. Consider future hardening (e.g., HMAC over content, monotonic nonce, or challenge-response key confirmation). *(Out of scope of current code; protocol surfaces can accommodate it by extending `<PAYLOAD>` and validating before ACK.)*

---


## Logging

A **thread-safe** `CommLogger` attaches to the client:

* Log calls: `logger.tx(...)`, `logger.rx(...)` (direction, timestamp UTC ISO-8601 `Z`, optional raw hex, optional `seq`).
* Save:

  * `logs.json` → **NDJSON** (one JSON per line)
  * `logs.csv` → CSV with header
  * `logs.txt` → compact human-readable lines
* In-memory dump: `logger.to_string(fmt="txt"|"json"|"csv")` — used by `RobotClient.history()` and CLI `history`. 

---

## Arduino Firmware overview

* **Board & baud:** Designed for ATmega328P-class boards (Uno/Nano). Uses `Serial.begin(9600)`.
* **No unsolicited events:** The Arduino never pushes messages on its own. The PC must **poll** (e.g., `STATUS`) to learn when motions finish.
* **Non-blocking motion:** M/R are executed by a tiny scheduler driven by `millis()`. Motions end automatically; motors are stopped in `updateMotion()`.

## Pin mapping (wiring)

| Function                               | Pin |
| -------------------------------------- | --- |
| Left motor PWM                         | D5  |
| Left motor DIR FWD                     | A0  |
| Left motor DIR REV                     | A1  |
| Left encoder (not used in demo logic)  | D2  |
| Right motor PWM                        | D6  |
| Right motor DIR FWD                    | A3  |
| Right motor DIR REV                    | A2  |
| Right encoder (not used in demo logic) | D3  |
| Sonar trigger                          | D11 |
| Sonar echo                             | D12 |
| IR analog left                         | A4  |
| IR digital left                        | D7  |
| IR analog right                        | A5  |
| IR digital right                       | D8  |

> Note: PWM pins must support `analogWrite`. Encoders are configured but not used in the time-based demo loop yet.

## Motion model & calibration

* **Time-based** (demo):

  * `MS_PER_CM = 18.0` → motion time = `abs(cm) * 18 ms`.
  * `MS_PER_DEG = 6.0` → rotation time = `abs(deg) * 6 ms`.
* **Speed:** default `basePWM = 140` (0..255).
* **Calibrate:** run on a flat surface:

  1. Send `V:160`, then `M:50`. If it overshoots, **increase** `MS_PER_CM`; if it undershoots, **decrease** it.
  2. Send `R:90`. Tune `MS_PER_DEG` similarly.
  3. When done, record tuned constants in the sketch.
## Firmware error codes (NACK)

* `BAD_CS` — checksum mismatch
* `BAD_FMT` — field count/format invalid (missing payload field, etc.)
* `BAD_SEQ` — sequence field not two hex digits
* `BAD_CMD` — unknown command
* `BAD_V` — invalid speed value

## Errors & Limitations

### Error handling (client)

* **Checksum mismatch** → treated as parse error → retry with backoff. 
* **Timeout waiting for frame** → retry with backoff until `max_retries`; then raise. 
* **`NACK BAD_CS`** → immediate resend of the **same** request (optimization). 
* **Other `NACK <ERROR>`** → raise runtime error to caller. 

### Limitations (firmware)

* Frame length guard at ~256 bytes on RX to avoid runaway frames. 
* 8-bit `SEQ` wraps (possible ambiguity if very long delays/interleaving occur). 
* XOR checksum detects random errors but is **not cryptographic** (no tamper resistance). See Security. 
* Time-based dead-reckoning (no encoders/PID yet).
* Max frame size 96 bytes (keep `STATUS`/`HELP` brief).
* XOR checksum (not cryptographic).
* No telemetry push—PC must poll.
---



