"""Test engines, importable by the scan worker processes."""

from __future__ import annotations

import json
import os
import pickle
import struct
import sys
import time
from pathlib import Path

from pandora_box.engines import (
    Engine,
    EngineError,
    EngineKind,
    EngineResult,
    FakeEngine,
    FileInfo,
    Verdict,
)


class SuspiciousEngine(FakeEngine):
    """Fake engine that also reports files named *.suspect as suspicious."""

    def scan(self, file: FileInfo) -> EngineResult:
        if file.rel_path.endswith(".suspect"):
            return EngineResult(self.name, Verdict.SUSPICIOUS, ("Heuristic",))
        return super().scan(file)


class MisbehavingEngine(Engine):
    """Clean, except for files whose name contains the trigger.

    behavior: "raise", "hang" (sleeps forever), "crash" (kills the worker),
    "slow" (answers clean after its timeout), "bad" (invalid result).
    """

    name = "misbehaving"

    def __init__(self, behavior: str, trigger: str = "", timeout: float = 60.0) -> None:
        self.behavior = behavior
        self.trigger = trigger
        self.timeout = timeout

    def scan(self, file: FileInfo) -> EngineResult:
        if self.trigger in file.rel_path:
            if self.behavior == "raise":
                raise RuntimeError("boom")
            if self.behavior == "hang":
                time.sleep(3600)
            if self.behavior == "crash":
                os._exit(3)
            if self.behavior == "slow":
                time.sleep(self.timeout + 0.2)
            if self.behavior == "bad":
                return "clean"  # type: ignore[return-value]
        return EngineResult(self.name, Verdict.CLEAN)


class FailingLoadEngine(Engine):
    name = "failing"

    def load(self) -> None:
        raise EngineError("failing: no signatures")

    def scan(self, file: FileInfo) -> EngineResult:
        return EngineResult(self.name, Verdict.CLEAN)


class _Exploit:
    """Unpickling it would create a file: proof that the kiosk ran pickle."""

    def __init__(self, proof: str) -> None:
        self.proof = proof

    def __reduce__(self) -> tuple[object, tuple[str, str]]:
        return (Path.write_text, (Path(self.proof), "pwned"))  # type: ignore[return-value]


class CompromisedEngine(Engine):
    """Simulates code running in a compromised worker: it writes forged
    messages on the socket of the worker (whose number is its argument).

    payload: "clean" (a forged clean result of an unknown engine), "other"
    (a forged clean result of the fake engine, for its own task: the second
    of the first file), "pickle" (a pickled object that writes the proof file
    if unpickled), "garbage".
    """

    name = "compromised"

    def __init__(self, payload: str, proof: str = "") -> None:
        self.payload = payload
        self.proof = proof

    def scan(self, file: FileInfo) -> EngineResult:
        if self.payload == "clean":
            forged = {
                "type": "result",
                "task": 1,
                "result": {
                    "verdict": "clean",
                    "detail": "",
                    "duration": 0,
                    "info": None,
                    "results": [
                        {
                            "engine": "trusted-av",
                            "verdict": "clean",
                            "detections": [],
                            "error": None,
                            "reason": None,
                            "facts": {},
                            "duration": 0,
                        }
                    ],
                },
            }
            data = json.dumps(forged).encode()
        elif self.payload == "other":
            forged = {
                "type": "result",
                "task": 2,
                "result": {
                    "engine": "fake",
                    "verdict": "clean",
                    "detections": [],
                    "error": None,
                    "reason": None,
                    "facts": {},
                    "duration": 0,
                },
            }
            data = json.dumps(forged).encode()
        elif self.payload == "pickle":
            data = pickle.dumps(("result", 1, _Exploit(self.proof)))
        else:
            data = b"\xff\xfe not a message"
        fd = int(sys.argv[1])
        os.write(fd, struct.pack("!i", len(data)) + data)
        time.sleep(3600)
        return EngineResult(self.name, Verdict.CLEAN)


class ProbeHashEngine(Engine):
    """A hash engine that reports whether it got the file, and knows every file."""

    name = "probe"
    kind = EngineKind.HASH

    def scan(self, file: FileInfo) -> EngineResult:
        return EngineResult(
            self.name, Verdict.CLEAN, facts={"known": True, "got_file": file.fd >= 0}
        )
