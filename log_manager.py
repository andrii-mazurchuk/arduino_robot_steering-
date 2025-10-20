from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional, Iterable
import threading
import json
import csv
import os

def _utcnow_iso() -> str:
    # ISO 8601 with milliseconds and explicit Z
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _to_str(msg) -> str:
    """Return a safe, human-readable string for msg (bytes→hex if undecodable)."""
    if msg is None:
        return ""
    if isinstance(msg, (bytes, bytearray)):
        try:
            return bytes(msg).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(msg).hex()
    return str(msg)

@dataclass
class LogEntry:
    ts_utc: str           # UTC timestamp (ISO 8601, Z)
    direction: str        # "TX" or "RX"
    message: str          # UTF-8 text if possible, else hex
    raw_hex: Optional[str] = None  # Raw bytes as hex (if supplied)
    seq: Optional[int] = None      # Optional protocol sequence number

class CommLogger:
    """Thread-safe logger for serial comms — minimal fields, easy API."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: List[LogEntry] = []

    # --- Simple API ----------------------------------------------------------
    def tx(self, message, *, raw: Optional[bytes] = None, seq: Optional[int] = None) -> None:
        """Log a transmitted (TX) message."""
        entry = LogEntry(
            ts_utc=_utcnow_iso(),
            direction="TX",
            message=_to_str(message),
            raw_hex=(raw.hex() if isinstance(raw, (bytes, bytearray)) else None),
            seq=seq,
        )
        with self._lock:
            self._entries.append(entry)

    def rx(self, message, *, raw: Optional[bytes] = None, seq: Optional[int] = None) -> None:
        """Log a received (RX) message."""
        entry = LogEntry(
            ts_utc=_utcnow_iso(),
            direction="RX",
            message=_to_str(message),
            raw_hex=(raw.hex() if isinstance(raw, (bytes, bytearray)) else None),
            seq=seq,
        )
        with self._lock:
            self._entries.append(entry)

    # --- Accessors -----------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(self) -> List[LogEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # --- Save ---------------------------------------------------------------
    def save(self, path: str) -> None:
        """
        Save logs. Format by extension:
          .json → NDJSON (one JSON per line)
          .csv  → CSV with header
          .txt  → compact human-readable text
        """
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            self._save_json(path)
        elif ext == ".csv":
            self._save_csv(path)
        elif ext == ".txt":
            self._save_txt(path)
        else:
            raise ValueError("Unsupported extension. Use .json, .csv, or .txt")

    def _save_json(self, path: str) -> None:
        with self._lock, open(path, "w", encoding="utf-8") as f:
            for e in self._entries:
                json.dump(asdict(e), f, ensure_ascii=False)
                f.write("\n")

    def _save_csv(self, path: str) -> None:
        with self._lock, open(path, "w", encoding="utf-8", newline="") as f:
            import csv
            writer = csv.DictWriter(f, fieldnames=["ts_utc", "direction", "message", "raw_hex", "seq"])
            writer.writeheader()
            for e in self._entries:
                writer.writerow(asdict(e))

    def _save_txt(self, path: str) -> None:
        with self._lock, open(path, "w", encoding="utf-8") as f:
            for e in self._entries:
                line = (
                    f"[{e.ts_utc}] {e.direction} {e.message!r}"
                    f"{' raw='+e.raw_hex if e.raw_hex else ''}"
                    f"{' seq='+str(e.seq) if e.seq is not None else ''}"
                )
                f.write(line + "\n")

    def to_string(self, fmt: str = "txt") -> str:
        """
        Return all logs as a single string in the chosen format.
        Supported: "txt" (human-readable), "json" (NDJSON), "csv".
        """
        fmt = fmt.lower().strip()
        with self._lock:
            entries = list(self._entries)

        if fmt == "txt":
            lines = []
            for e in entries:
                line = (
                    f"[{e.ts_utc}] {e.direction} {e.message!r}"
                    f"{' raw=' + e.raw_hex if e.raw_hex else ''}"
                    f"{' seq=' + str(e.seq) if e.seq is not None else ''}"
                )
                lines.append(line)
            return "\n".join(lines)

        elif fmt == "json":
            import json
            return "\n".join(json.dumps(asdict(e), ensure_ascii=False) for e in entries)

        elif fmt == "csv":
            import csv
            import io
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=["ts_utc", "direction", "message", "raw_hex", "seq"])
            writer.writeheader()
            for e in entries:
                writer.writerow(asdict(e))
            return buf.getvalue()

        else:
            raise ValueError('Unsupported format. Use "txt", "json", or "csv".')