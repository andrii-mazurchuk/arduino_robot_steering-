#!/usr/bin/env python3
"""
Interactive CLI for the RobotClient defined in robot_client.py

Features:
- Connect using --port and --baud
- One-shot commands via arguments OR an interactive REPL shell
- Friendly help and command hints
- Command aliases (e.g., v 160, m 25, r -90)
- Save log to file:  save-log logs.txt
- Reconnect on demand: reconnect [--port COMx] [--baud 115200]
- Exit: quit / exit / Ctrl-D

Usage examples:
  python robot_cli.py --port COM6
  python robot_cli.py --port /dev/ttyUSB0 --baud 115200
  python robot_cli.py --port COM7 "PING" "V:160" "M:20" "B" "STATUS"
  python robot_cli.py --port COM7 --script cmds.txt
"""
import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Optional

# IMPORTANT: we do NOT modify robot_client.py, only import it.
from robot_client import RobotClient

# ---------------------------
# Helpers
# ---------------------------
def print_ok(msg: str):    print(f"\033[92m✔ {msg}\033[0m")
def print_info(msg: str):  print(f"\033[94mℹ {msg}\033[0m")
def print_warn(msg: str):  print(f"\033[93m! {msg}\033[0m")
def print_err(msg: str):   print(f"\033[91m✘ {msg}\033[0m")

def parse_token(token: str):
    """
    Accepts both 'CMD' and 'CMD:payload' forms.
    Also supports aliases like: v 160  -> ('V','160')
    """
    token = token.strip()
    if ':' in token:
        c, p = token.split(':', 1)
        return c.strip().upper(), p.strip()
    return token.strip().upper(), ""

# ---------------------------
# REPL Shell
# ---------------------------
class RobotShell:
    PROMPT = "\033[96mrobot>\033[0m "

    def __init__(self, rc: RobotClient):
        self.rc = rc

    # ---- Command dispatch ----
    def do_help(self, *_):
        print_info(
            "Available commands:\n"
            "  ping                       - health check\n"
            "  help                       - show this help\n"
            "  status                     - robot status\n"
            "  v <0..255> or V:<num>      - set linear speed (PWM)\n"
            "  m <cm> or M:<cm>           - move by centimeters (+forward, -back)\n"
            "  r <deg> or R:<deg>         - rotate by degrees (+right, -left)\n"
            "  s or S                     - emergency stop\n"
            "  b or B                     - sonar read (cm)\n"
            "  i or I                     - IR sensor read\n"
            "  history                    - show in-memory comm log\n"
            "  save-log <path>            - write comm log to file\n"
            "  reconnect [--port P] [--baud B]  - reopen serial (with PING check)\n"
            "  quit / exit                - exit the CLI\n"
            "\n"
            "You can also enter raw tokens like:  PING  V:160  M:20  R:-90  B  STATUS  S"
        )

    def do_ping(self, *_):          self._run(lambda: self.rc.ping(), "PING")
    def do_status(self, *_):        self._run(lambda: self.rc.status(), "STATUS")
    def do_history(self, *_):       print(self.rc.history() or "<empty>")

    def do_v(self, *args):          self._need_arg(args, "v <0..255>"); self._run(lambda: self.rc.set_v(int(args[0])), f"V {args[0]}")
    def do_m(self, *args):          self._need_arg(args, "m <cm>");     self._run(lambda: self.rc.move_cm(int(args[0])), f"M {args[0]}")
    def do_r(self, *args):          self._need_arg(args, "r <deg>");    self._run(lambda: self.rc.rotate_deg(int(args[0])), f"R {args[0]}")
    def do_s(self, *_):             self._run(lambda: self.rc.stop(), "S")
    def do_b(self, *_):             self._run(lambda: self.rc.sonar(), "B")
    def do_i(self, *_):             self._run(lambda: self.rc.ir(), "I")

    def do_save_log(self, *args):
        self._need_arg(args, "save-log <path>")
        path = Path(args[0]).expanduser()
        try:
            data = self.rc.history()
            path.write_text(data, encoding="utf-8")
            print_ok(f"Log saved to {path}")
        except Exception as e:
            print_err(f"Failed to save log: {e}")

    def do_reconnect(self, *args):
        # Basic flag parsing (without external libs)
        port: Optional[str] = None
        baud: Optional[int] = None
        toks = list(args)
        i = 0
        while i < len(toks):
            if toks[i] == "--port" and i + 1 < len(toks):
                port = toks[i + 1]
                i += 2
                continue
            if toks[i] == "--baud" and i + 1 < len(toks):
                try:
                    baud = int(toks[i + 1])
                except ValueError:
                    print_warn("Invalid baud; ignored")
                i += 2
                continue
            i += 1

        _, ok = self.rc.reconnect_serial(port=port, baudrate=baud, do_ping_check=True)
        if ok:
            print_ok("Reconnected and alive.")
        else:
            print_err("Reconnect failed.")

    def do_quit(self, *_): raise EOFError()
    def do_exit(self, *_): raise EOFError()

    # ---- Core REPL loop ----
    def loop(self):
        self.do_help()
        while True:
            try:
                line = input(self.PROMPT)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue

            line = line.strip()
            if not line:
                continue

            # Allow raw token form like "V:160"
            if ":" in line and " " not in line:
                cmd, payload = parse_token(line)
                self._dispatch_token(cmd, payload)
                continue

            # Parse as "cmd args..."
            try:
                parts = shlex.split(line)
            except ValueError as e:
                print_warn(f"Parse error: {e}")
                continue
            if not parts:
                continue

            cmd = parts[0].lower()
            args = parts[1:]

            # Aliases
            if cmd in {"h", "?", "help"}:            self.do_help()
            elif cmd in {"ping"}:                    self.do_ping()
            elif cmd in {"status"}:                  self.do_status()
            elif cmd in {"v"}:                       self.do_v(*args)
            elif cmd in {"m"}:                       self.do_m(*args)
            elif cmd in {"r"}:                       self.do_r(*args)
            elif cmd in {"s"}:                       self.do_s()
            elif cmd in {"b"}:                       self.do_b()
            elif cmd in {"i"}:                       self.do_i()
            elif cmd in {"history"}:                 self.do_history()
            elif cmd in {"save-log", "savelog"}:     self.do_save_log(*args)
            elif cmd in {"reconnect"}:               self.do_reconnect(*args)
            elif cmd in {"quit", "exit"}:            break
            else:
                # Fallback: try raw token "CMD[:payload]" split by colon and spaces
                if ":" in cmd:
                    c, p = parse_token(cmd)
                    self._dispatch_token(c, p)
                else:
                    print_warn("Unknown command. Type 'help'.")

    # ---- Utilities ----
    def _dispatch_token(self, c: str, p: str):
        try:
            if c == "PING":       self._run(lambda: self.rc.ping(), "PING")
            elif c == "HELP":     self.do_help()
            elif c == "STATUS":   self._run(lambda: self.rc.status(), "STATUS")
            elif c == "HISTORY":  self.do_history()
            elif c == "V":        self._run(lambda: self.rc.set_v(int(p or "0")), f"V {p}")
            elif c == "M":        self._run(lambda: self.rc.move_cm(int(p or "0")), f"M {p}")
            elif c == "R":        self._run(lambda: self.rc.rotate_deg(int(p or "0")), f"R {p}")
            elif c == "S":        self._run(lambda: self.rc.stop(), "S")
            elif c == "B":        self._run(lambda: self.rc.sonar(), "B")
            elif c == "I":        self._run(lambda: self.rc.ir(), "I")
            else:
                print_warn(f"Unknown token '{c}'. Type 'help'.")
        except Exception as e:
            print_err(f"Error: {e}")

    def _run(self, fn, label: str):
        try:
            resp = fn()
            print_ok(f"{label} -> {resp}")
        except Exception as e:
            print_err(f"{label} failed: {e}")

    @staticmethod
    def _need_arg(args, usage):
        if not args or args[0] == "":
            raise ValueError(f"Missing argument. Usage: {usage}")

# ---------------------------
# Main
# ---------------------------
def run_batch(rc: RobotClient, tokens):
    for token in tokens:
        token = token.strip()
        if not token or token.startswith("#"):
            continue
        c, p = parse_token(token) if ":" in token else (token.upper(), "")
        try:
            if c == "PING":      print(rc.ping())
            elif c == "HELP":    print("See 'help' in interactive mode.")
            elif c == "STATUS":  print(rc.status())
            elif c == "HISTORY": print(rc.history())
            elif c == "V":       print(rc.set_v(int(p)))
            elif c == "M":       print(rc.move_cm(int(p)))
            elif c == "R":       print(rc.rotate_deg(int(p)))
            elif c == "S":       print(rc.stop())
            elif c == "B":       print(rc.sonar())
            elif c == "I":       print(rc.ir())
            else:
                print_warn(f"Unknown token: {token}")
        except Exception as e:
            print_err(f"{token} -> {e}")

def main():
    ap = argparse.ArgumentParser(description="Interactive CLI for robot_client.RobotClient (no modifications to robot_client.py).")
    ap.add_argument("--port", required=True, help="Serial port, e.g. COM6 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600, help="Baudrate (default: 9600)")
    ap.add_argument("--script", type=str, help="Path to a file with one command per line")
    ap.add_argument("tokens", nargs="*", help="Optional one-shot tokens (e.g., PING V:160 M:20)")
    args = ap.parse_args()

    # Instantiate the client
    rc = RobotClient(args.port, args.baud, max_retries=10)

    # Batch (script file) mode
    if args.script:
        path = Path(args.script).expanduser()
        if not path.exists():
            print_err(f"Script file not found: {path}")
            sys.exit(2)
        lines = path.read_text(encoding="utf-8").splitlines()
        run_batch(rc, lines)
        return

    # One-shot tokens
    if args.tokens:
        run_batch(rc, args.tokens)
        return

    # Interactive REPL
    print_info(f"Connected on {args.port} @ {args.baud} bps")
    shell = RobotShell(rc)
    try:
        shell.loop()
    except EOFError:
        pass
    print_info("Bye!")

if __name__ == "__main__":
    main()
