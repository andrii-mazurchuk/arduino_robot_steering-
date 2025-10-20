"""
Robot client: framed serial with checksum, ACK/NACK, retries & exponential backoff.
Protocol:
  Request  ^<SEQ>|<CMD>|<PAYLOAD>*<CS>$
  Response ^<SEQ>|ACK|<INFO>*<CS>$   or   ^<SEQ>|NACK|<ERROR>*<CS>$

Commands:
  PING, HELP, STATUS, V:<0..255>, M:<cm>, R:<deg>, S, B, I
"""

import serial
import time
import argparse
import sys

# from app import send_command

from log_manager import CommLogger

START = b'^'
END   = b'$'

def xor_checksum(b: bytes) -> int:
    cs = 0
    for x in b:
        cs ^= x
    return cs & 0xFF

def to_hex2(v: int) -> str:
    return f"{v:02X}"

def build_frame(seq: int, cmd: str, payload: str = "") -> bytes:
    content = f"{to_hex2(seq)}|{cmd}|{payload}".encode('ascii')
    cs = xor_checksum(content)
    frame = b'^' + content + b'*' + f"{cs:02X}".encode('ascii') + b'$'
    return frame

def parse_frame(buf: bytes):
    """
    Returns (seq:int, cmd:str, payload:str) or raises ValueError
    """
    if not (buf.startswith(b'^') and buf.endswith(b'$')):
        raise ValueError("Bad markers")
    if b'*' not in buf:
        raise ValueError("No checksum sep")
    star = buf.rfind(b'*')
    content = buf[1:star]  # exclude '^' and '*'
    cs_hex = buf[star+1:-1]  # exclude '$'
    if len(cs_hex) != 2:
        raise ValueError("Bad CS length")
    try:
        got_cs = int(cs_hex.decode('ascii'), 16)
    except Exception:
        raise ValueError("Bad CS hex")
    calc_cs = xor_checksum(content)
    if calc_cs != got_cs:
        raise ValueError("Checksum mismatch")
    parts = content.decode('ascii').split('|')
    if len(parts) != 3:
        raise ValueError("Bad content fields")
    seq_hex, cmd, payload = parts
    try:
        seq = int(seq_hex, 16)
    except Exception:
        raise ValueError("Bad seq")
    return seq, cmd, payload

def open_serial(port: str, baud: int, timeout: float = 0.5) -> serial.Serial:
    ser = serial.Serial(port, baudrate=baud, timeout=timeout)
    # small warm-up
    time.sleep(1.5)
    return ser

def recv_frame(ser: serial.Serial, timeout: float) -> bytes:
    """Read one framed message by scanning for '^'...'$', honoring timeout."""
    ser.timeout = 0.05
    start_time = time.time()
    buf = bytearray()
    in_frame = False
    while time.time() - start_time < timeout:
        b = ser.read(1)
        if not b:
            continue
        c = b[0]
        if not in_frame:
            if c == ord('^'):
                in_frame = True
                buf.clear()
                buf.append(c)
        else:
            buf.append(c)
            if c == ord('$'):
                return bytes(buf)
            if len(buf) > 256:  # guard
                in_frame = False
                buf.clear()
    raise TimeoutError("Recv timeout")

class RobotClient:
    def __init__(self, port: str, baud: int = 9600, base_timeout: float = 0.6, max_retries: int = 3):
        self.logger = CommLogger()
        self.ser = open_serial(port, baud)
        self.seq = 0
        self.base_timeout = base_timeout
        self.max_retries = max_retries

    def next_seq(self) -> int:
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    def request(self, cmd: str, payload: str = " ") -> str:
        seq = self.next_seq()
        frame = build_frame(seq, cmd, payload)
        text = frame.decode('utf-8')
        backoff = self.base_timeout
        for attempt in range(1, self.max_retries + 1):
            # send
            self.logger.tx(text, raw=frame, seq=seq)
            self.ser.write(frame)
            # wait response
            try:
                raw = recv_frame(self.ser, timeout=backoff)
                r_seq, r_cmd, r_payload = parse_frame(raw)

                message =f"{r_seq}|{r_cmd}|{r_payload}"
                self.logger.tx(message, raw=raw, seq=r_seq)

                if r_seq != seq:
                    # mismatched seq - ignore and keep waiting within same attempt
                    continue
                if r_cmd == "ACK":
                    return r_payload  # success
                elif r_cmd == "NACK":
                    if r_payload == "BAD_CS":
                        return self.request(cmd, payload)
                    raise RuntimeError(f"NACK: {r_payload}")
                else:
                    # unexpected message type, continue waiting
                    continue
            except TimeoutError:
                # retry with backoff
                backoff *= 2
            except ValueError as e:
                # parse/CS error, retry
                backoff *= 2
        raise TimeoutError(f"No valid ACK after {self.max_retries} tries")




    # Convenience wrappers
    def ping(self) -> str: return self.request("PING")
    def help(self) -> str: return self.request("HELP")
    def status(self) -> str: return self.request("STATUS")
    def set_v(self, pwm: int) -> str: return self.request("V", str(int(pwm)))
    def move_cm(self, cm: int) -> str: return self.request("M", str(int(cm)))
    def rotate_deg(self, deg: int) -> str: return self.request("R", str(int(deg)))
    def stop(self) -> str: return self.request("S")
    def sonar(self) -> str: return self.request("B")
    def ir(self) -> str: return self.request("I")

    def history(self) -> str: return self.logger.to_string()


    def is_link_alive(self, timeout_s=1.0):
        """
        Checks if the serial connection is still alive by sending a PING request.
        Returns True if the connection responds with ACK, otherwise False.
        """
        if self.ser is None or not getattr(self.ser, "is_open", False):
            return False

        old_timeout = self.ser.timeout
        self.ser.timeout = timeout_s
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            line = self.ping()
            return line == "ACK"
        except Exception:
            return False
        finally:
            self.ser.timeout = old_timeout

    def reconnect_serial(self,
                         port=None,
                         baudrate=None,
                         max_retries=5,
                         base_delay=0.5,
                         open_timeout=1.0,
                         do_ping_check=True):
        """
        Attempts to restore the serial connection (reconnect) using previous settings
        unless new port/baudrate values are provided. Implements exponential backoff
        for retry delays.

        Returns a tuple (ser, ok) where:
          - ser : the Serial object (new or reused if reconnect succeeded)
          - ok  : bool indicating if the connection is ready for use
        """

        # If the current connection is active and alive, keep using it
        if self.ser is not None and getattr(self.ser, "is_open", False):
            if not do_ping_check or self.is_link_alive():
                return self.ser, True
            # Connection handle exists, but the link is unresponsive → close before retry
            try:
                self.ser.close()
            except Exception:
                pass

        # Use previous connection parameters if new ones are not provided
        prev_port = getattr(self.ser, "port", None) if self.ser is not None else None
        prev_baud = getattr(self.ser, "baudrate", None) if self.ser is not None else None
        port = port or prev_port
        baudrate = baudrate or prev_baud or 9600


        # Retry loop with exponential backoff
        last_exc = None
        for attempt in range(max_retries):
            delay = base_delay * (2 ** attempt) if attempt > 0 else 0
            if delay:
                time.sleep(delay)

            try:
                self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=open_timeout)

                # Optional soft reset via DTR – often helps after a lost connection
                try:
                    self.ser.dtr = False
                    time.sleep(0.05)
                    self.ser.dtr = True
                except Exception:
                    pass

                # Arduino usually resets after opening the port – give it time to restart
                time.sleep(2.0)
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()

                # Optionally test connection health after opening
                if not do_ping_check or self.is_link_alive():
                    print(f"Reconnected on {port} @ {baudrate} bps")
                    return self.ser, True
                else:
                    # No ACK received after reconnect attempt – close and retry
                    self.ser.close()
                    last_exc = RuntimeError("No ACK after reopen")
            except Exception as e:
                last_exc = e

        print(f"Reconnect failed after {max_retries} attempts. Last error: {last_exc}")
        return self.ser, False


def main():
    ap = argparse.ArgumentParser(description="Robot serial client with checksum and retries")
    ap.add_argument("--port", required=True, help="Serial port, e.g. COM6 or /dev/ttyUSB0", default="COM7")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("cmd", nargs='*', help="Commands to send in order, e.g. PING V:160 M:20 B STATUS S")
    args = ap.parse_args()

    rc = RobotClient(args.port, args.baud)

    if not args.cmd:
        # demo sequence
        cmds = ["PING", "HELP", "STATUS", "V:160", "M:20", "R:-90", "B", "I", "S", "STATUS"]
    else:
        cmds = args.cmd

    for token in cmds:
        if ':' in token:
            c, p = token.split(':', 1)
        else:
            c, p = token, ""
        c = c.strip().upper()
        try:
            if c == "PING":
                resp = rc.ping()
            elif c == "HELP":
                resp = rc.help()
            elif c == "STATUS":
                resp = rc.status()
            elif c == "HISTORY":
                resp = rc.history()
            elif c == "V":
                resp = rc.set_v(int(p))
            elif c == "M":
                resp = rc.move_cm(int(p))
            elif c == "R":
                resp = rc.rotate_deg(int(p))
            elif c == "S":
                resp = rc.stop()
            elif c == "B":
                resp = rc.sonar()
            elif c == "I":
                resp = rc.ir()
            else:
                print(f">>> Unknown command token: {token}")
                continue
            print(f"<< {c}: {resp}")
        except Exception as e:
            print(f"<< ERROR for {token}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
