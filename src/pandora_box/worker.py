"""Scan worker process, started by the kiosk (pandora_box.workers).

    python -m pandora_box.worker FD

Each worker loads one engine: a compromised engine cannot forge the results
of another one. FD is one end of a Unix socket pair. The kiosk sends pickled
messages (the worker trusts the kiosk); the worker answers with JSON
messages (the kiosk does not trust the worker, see pandora_box.protocol):

    kiosk -> worker                     worker -> kiosk
    ("init", spec, seccomp)             {"type": "ready", "engines": [...]}
                                        {"type": "load_failed", "error": ...}
    ("info",)                           {"type": "info", "engines": [...]}
    ("scan", task_id, info) [+ fd]      {"type": "result", "task": id, "result": {...},
                                         "mime": ..., "description": ...}
    ("stop",)

info is the FileInfo of the file, hashed by the kiosk. A content engine
also gets the descriptor of the file opened by the kiosk, and identifies its
type (libmagic); a hash engine never gets the file.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import signal
import sys
from multiprocessing.connection import Connection
from multiprocessing.reduction import recv_handle
from typing import Any

from pandora_box.engines import EngineKind, EngineResult, Verdict
from pandora_box.engines.registry import load_engines
from pandora_box.filetype import FileTypeDetector
from pandora_box.logs import LOGGER_NAME
from pandora_box.pipeline import run_engine
from pandora_box.protocol import (
    describe_engine,
    encode,
    engine_info_message,
    engine_result_message,
)


def serve(conn: Connection) -> int:
    def send(message: dict[str, Any]) -> None:
        conn.send_bytes(encode(message))

    _, spec, seccomp = conn.recv()
    try:
        [engine] = load_engines([spec])
        content = engine.kind is EngineKind.CONTENT
        detector = FileTypeDetector() if content else None
        if seccomp:
            from pandora_box.seccomp import apply_filter

            apply_filter()
    except Exception as ex:
        send({"type": "load_failed", "error": str(ex)})
        return 1
    send({"type": "ready", "engines": engine_info_message([describe_engine(engine)])})
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return 0
        if message[0] == "stop":
            return 0
        if message[0] == "info":
            send({"type": "info", "engines": engine_info_message([describe_engine(engine)])})
        elif message[0] == "scan":
            _, task_id, info = message
            mime = description = None
            if detector is None:
                result = run_engine(engine, info)
            else:
                fd = recv_handle(conn)
                try:
                    file_type = detector.identify(fd)
                    mime, description = file_type.mime, file_type.description
                    file = dataclasses.replace(info, mime=mime, description=description, fd=fd)
                    result = run_engine(engine, file)
                except OSError as ex:
                    error = f"cannot read: {ex.strerror or ex}"
                    result = EngineResult(engine.name, Verdict.ERROR, error=error)
                finally:
                    os.close(fd)
            send(
                {
                    "type": "result",
                    "task": task_id,
                    "result": engine_result_message(result),
                    "mime": mime,
                    "description": description,
                }
            )


def main(argv: list[str]) -> int:
    # Ctrl-C is handled by the kiosk process, which stops the workers
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # Workers do not log: everything they find goes to the kiosk process
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return serve(Connection(int(argv[0])))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
