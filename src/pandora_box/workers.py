"""Scan worker processes, one engine each, supervised by a watchdog.

Each worker is a long-lived process (pandora_box.worker) that loads one
engine once, then scans files one at a time: a compromised engine (a crafted
file exploiting YARA-X, libmagic...) cannot forge the result of another
engine, and a crash only affects that engine for the file being scanned.
With scan.sandbox, workers run in a bubblewrap sandbox under a dedicated
user (pandora_box.sandbox).

For each file, the kiosk (this module, in the kiosk process):

1. opens it (pandora_box.inventory.open_entry) and hashes it itself: the hash
   engines and the scan report get hashes no worker can forge;
2. asks the worker of each hash engine (MalwareBazaar, Hashlookup), with the
   hashes only: these workers never get the file;
3. unless the file is known and content engines are skipped for known files,
   passes its descriptor to a worker of each content engine (ClamAV, YARA),
   which never opens a file of the device;
4. combines the results itself (pandora_box.policy). A worker only reports
   the result of its own engine, as validated JSON (pandora_box.protocol); a
   malformed answer is handled like a crash.

Hash engines have one worker each, content engines scan.workers each: up to
scan.workers files are scanned at the same time.

concurrent.futures.ProcessPoolExecutor is not used: it cannot cancel a running
task, and a killed worker breaks the whole pool. Here, the supervisor arms two
deadlines for each engine of a file:

- the engine deadline: the timeout of the engine plus a grace delay;
- the file deadline: scan.file_timeout, for the whole file.

When a deadline expires the worker is killed (SIGKILL), the engine result is
an error and a new worker is started. A worker that dies (crash, out of
memory) is handled the same way. A timeout or a crash is never clean.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import multiprocessing.connection
import os
import socket
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from multiprocessing.reduction import send_handle
from pathlib import Path
from typing import Any

from pandora_box.engines import EngineError, EngineKind, EngineResult, EngineSpec, FileInfo, Verdict
from pandora_box.hashing import hash_fd
from pandora_box.inventory import Entry, UnsafeFileError, open_entry
from pandora_box.logs import get_logger, log_event
from pandora_box.pipeline import KNOWN_FILE, PipelineOptions, file_detail, skip_content
from pandora_box.policy import aggregate_file
from pandora_box.protocol import (
    MAX_MESSAGE_SIZE,
    EngineInfo,
    ProtocolError,
    decode,
    decode_engine_result,
    decode_engines,
    decode_file_type,
    describe_engine,
)
from pandora_box.results import FileResult
from pandora_box.sandbox import Sandbox
from pandora_box.text import escape

__all__ = ["EngineInfo", "WorkerPool", "describe_engine"]

logger = get_logger("workers")

# Added to the timeout of an engine before the watchdog kills the worker:
# engines with a native timeout (YARA-X, clamd socket) report it themselves
ENGINE_GRACE = 5.0
# Maximum time for a worker to load its engine
START_TIMEOUT = 600.0


# Memory left to clamd and the system, then needed by each file scanned at the
# same time (a YARA-X worker holds its compiled rules: about 200 MB with YARA
# Forge), in MB
RESERVED_MEMORY = 1536
MEMORY_PER_WORKER = 256
MAX_AUTO_WORKERS = 16


def auto_workers(meminfo: Path = Path("/proc/meminfo"), cpus: int | None = None) -> int:
    """Files scanned at the same time: one per CPU, as many as the memory allows."""
    cpus = cpus or os.cpu_count() or 1
    try:
        total = next(
            int(line.split()[1]) // 1024
            for line in meminfo.read_text().splitlines()
            if line.startswith("MemTotal:")
        )
    except (OSError, StopIteration, ValueError, IndexError):
        return min(cpus, 2)
    by_memory = (total - RESERVED_MEMORY) // MEMORY_PER_WORKER
    return max(1, min(cpus, by_memory, MAX_AUTO_WORKERS))


# -- supervisor side ---------------------------------------------------------------


class _State(Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    # The worker could not load its engine: it is not restarted
    FAILED = "failed"


@dataclass
class _File:
    """A file being scanned: its hashes, then the result of each engine."""

    entry: Entry
    info: FileInfo
    start: float
    deadline: float
    # Engines not asked yet, in order (hash engines first)
    pending: list[str]
    results: dict[str, EngineResult] = field(default_factory=dict)
    mime: str | None = None
    description: str | None = None


@dataclass
class _Task:
    task_id: int
    file: _File
    engine: str
    sent: float
    deadline: float


@dataclass
class _Worker:
    spec: EngineSpec
    # Index of its spec; name of its engine, as reported when first started
    index: int
    process: subprocess.Popen[bytes]
    conn: multiprocessing.connection.Connection
    state: _State = _State.STARTING
    started: float = field(default_factory=time.monotonic)
    task: _Task | None = None
    name: str = ""


ResultCallback = Callable[[FileResult], None]


class WorkerPool:
    """Scan worker processes, one engine each, reused for every device."""

    def __init__(
        self,
        specs: Sequence[EngineSpec],
        options: PipelineOptions,
        workers: int,
        file_timeout: float,
        engine_grace: float = ENGINE_GRACE,
        start_timeout: float = START_TIMEOUT,
        sandbox: Sandbox | None = None,
    ) -> None:
        self.specs = tuple(specs)
        self.options = options
        self.size = workers
        self.file_timeout = file_timeout
        self.engine_grace = engine_grace
        self.start_timeout = start_timeout
        self.sandbox = sandbox
        self._workers: list[_Worker] = []
        self._next_task = 0
        self.engines: list[EngineInfo] = []
        # Number of workers restarted after a timeout or a crash
        self.restarts = 0

    # -- life cycle -------------------------------------------------------------

    def start(self) -> list[EngineInfo]:
        """Start the workers and wait until they have loaded their engine.

        Raise EngineError when an engine cannot be loaded.
        """
        if self.sandbox is not None:
            self.sandbox.prepare()
        self._workers = [
            self._spawn(spec, index)
            for index, spec in enumerate(self.specs)
            for _ in range(1 if spec.factory_kind() is EngineKind.HASH else self.size)
        ]
        info: dict[int, EngineInfo] = {}
        deadline = time.monotonic() + self.start_timeout
        while starting := [w for w in self._workers if w.state is _State.STARTING]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.stop()
                raise EngineError("scan workers did not load their engines in time")
            for conn in multiprocessing.connection.wait([w.conn for w in starting], remaining):
                worker = next(w for w in starting if w.conn is conn)
                error = self._receive_ready(worker, info)
                if error is not None:
                    self.stop()
                    raise EngineError(error)
        self.engines = [info[index] for index in range(len(self.specs))]
        names = [e.name for e in self.engines]
        if len(set(names)) != len(names):
            self.stop()
            raise EngineError(f"two engines with the same name: {', '.join(names)}")
        log_event(
            logger,
            "workers_started",
            workers=len(self._workers),
            engines=[e.name for e in self.engines],
        )
        for engine in self.engines:
            excluded = engine.extra.get("excluded_rules") or []
            if excluded:
                log_event(
                    logger,
                    "rules_excluded",
                    logging.WARNING,
                    engine=engine.name,
                    count=len(excluded),
                    errors=excluded,
                )
        return self.engines

    def stop(self) -> None:
        for worker in self._workers:
            with contextlib.suppress(OSError, ValueError):
                worker.conn.send(("stop",))
        for worker in self._workers:
            with contextlib.suppress(subprocess.TimeoutExpired):
                worker.process.wait(timeout=2)
            self._kill(worker)
        self._workers = []

    def __enter__(self) -> WorkerPool:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def pids(self) -> list[int]:
        """Process ids of the running workers (diagnostics and tests)."""
        return [w.process.pid for w in self._workers]

    def _spawn(self, spec: EngineSpec, index: int) -> _Worker:
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        command = [sys.executable, "-m", "pandora_box.worker", str(child.fileno())]
        if self.sandbox is not None:
            command = self.sandbox.wrap(command)
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            # The modules of the engine factories (tests and development)
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        }
        with child:
            # stdout may be the kiosk screen. Workers never write, but errors
            # of bubblewrap or Python go to stderr: the journal of the service
            process = subprocess.Popen(  # noqa: S603  (fixed command)
                command,
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                env=env,
            )
        # Our copy of the child end is closed: EOF then signals a dead worker
        conn = multiprocessing.connection.Connection(parent.detach())
        worker = _Worker(spec, index, process, conn)
        # A dead worker is reported when its first message is read
        with contextlib.suppress(OSError):
            conn.send(("init", spec, self.sandbox is not None))
        return worker

    def _receive(self, worker: _Worker) -> dict[str, Any]:
        """Read and decode a message: ProtocolError, EOFError or OSError."""
        return decode(worker.conn.recv_bytes(MAX_MESSAGE_SIZE))

    def _receive_ready(self, worker: _Worker, info: dict[int, EngineInfo]) -> str | None:
        """Handle the first message of a worker; return an error message."""
        try:
            message = self._receive(worker)
            if message["type"] == "load_failed":
                return str(message.get("error"))[:1000]
            if message["type"] != "ready":
                return f"unexpected message from a scan worker: {message['type'][:100]}"
            engines = decode_engines(message.get("engines"))
        except (EOFError, OSError):
            return f"scan worker died while loading engines (exit code {self._exit(worker)})"
        except ProtocolError as ex:
            return f"invalid message from a scan worker: {ex}"
        # A worker describes its one engine, the same after a restart
        if len(engines) != 1 or (worker.name and engines[0].name != worker.name):
            return f"scan worker of {worker.name or worker.spec.name} reported other engines"
        worker.name = engines[0].name
        worker.state = _State.IDLE
        info[worker.index] = engines[0]
        return None

    # -- information ------------------------------------------------------------

    def engine_info(self) -> list[EngineInfo]:
        """Ask an idle worker of each engine for up to date information."""
        info = {e.name: e for e in self.engines}
        for name in info:
            for worker in self._workers:
                if worker.name != name or worker.state is not _State.IDLE:
                    continue
                try:
                    worker.conn.send(("info",))
                    if worker.conn.poll(30):
                        message = self._receive(worker)
                        engines = decode_engines(message.get("engines"))
                        if message["type"] == "info" and [e.name for e in engines] == [name]:
                            info[name] = engines[0]
                            break
                except (EOFError, OSError, ProtocolError):
                    continue
        self.engines = [info[e.name] for e in self.engines]
        return self.engines

    def _kind(self, name: str) -> EngineKind:
        return next(e.kind for e in self.engines if e.name == name)

    def _timeout(self, name: str) -> float:
        # The timeout reported at startup, never a new one from a worker
        return next(e.timeout for e in self.engines if e.name == name)

    # -- scan ---------------------------------------------------------------------

    def scan(self, root: Path, entries: Iterable[Entry], on_result: ResultCallback) -> None:
        """Scan the entries; on_result is called in this thread for each file."""
        queue = deque(entries)
        files: list[_File] = []
        while queue or files:
            for worker in self._workers:
                if worker.state is _State.IDLE and worker.process.poll() is not None:
                    self._kill(worker)
                    self._restart(worker)
            while queue and len(files) < self.size:
                file = self._begin(root, queue.popleft(), on_result)
                if file is not None:
                    files.append(file)
            for file in files:
                self._dispatch(root, file)
            for file in [f for f in files if self._done(f)]:
                files.remove(file)
                on_result(self._finish(root, file))
            if files:
                self._wait(root)

    def _begin(self, root: Path, entry: Entry, on_result: ResultCallback) -> _File | None:
        """Hash a file in this process; None when it cannot be scanned."""
        start = time.monotonic()
        try:
            with open_entry(root, entry) as fd:
                hashes = hash_fd(fd)
        except UnsafeFileError as ex:
            on_result(self._error_result(root, entry, str(ex)))
            return None
        except OSError as ex:
            on_result(self._error_result(root, entry, f"cannot read: {ex.strerror or ex}"))
            return None
        if hashes.size != entry.size:
            on_result(self._error_result(root, entry, "file size changed while reading"))
            return None
        info = FileInfo(
            rel_path=entry.rel_path,
            size=hashes.size,
            sha256=hashes.sha256,
            sha1=hashes.sha1,
            md5=hashes.md5,
        )
        hash_first = sorted(
            (e.name for e in self.engines), key=lambda n: self._kind(n) is not EngineKind.HASH
        )
        return _File(entry, info, start, start + self.file_timeout, hash_first)

    def _dispatch(self, root: Path, file: _File) -> None:
        """Pass the file to an idle worker of each engine it still needs."""
        for name in list(file.pending):
            content = self._kind(name) is EngineKind.CONTENT
            if content:
                # The content engines wait for the hash engines
                hash_names = [e.name for e in self.engines if e.kind is EngineKind.HASH]
                if not all(n in file.results for n in hash_names):
                    return
                if skip_content([file.results[n] for n in hash_names], self.options):
                    file.results[name] = EngineResult(name, Verdict.SKIPPED, reason=KNOWN_FILE)
                    file.pending.remove(name)
                    continue
            group = [w for w in self._workers if w.name == name]
            if all(w.state is _State.FAILED for w in group):
                # No worker can load this engine any more: fail closed
                file.results[name] = EngineResult(
                    name, Verdict.ERROR, error="no scan worker available"
                )
                file.pending.remove(name)
                continue
            worker = next((w for w in group if w.state is _State.IDLE), None)
            if worker is not None:
                self._assign(worker, root, file, name, content)

    def _assign(self, worker: _Worker, root: Path, file: _File, name: str, content: bool) -> None:
        self._next_task += 1
        now = time.monotonic()
        deadline = min(file.deadline, now + self._timeout(name) + self.engine_grace)
        task = _Task(self._next_task, file, name, now, deadline)
        try:
            if content:
                with open_entry(root, file.entry) as fd:
                    worker.conn.send(("scan", task.task_id, file.info))
                    send_handle(worker.conn, fd, worker.process.pid)
            else:
                worker.conn.send(("scan", task.task_id, file.info))
        except UnsafeFileError as ex:
            file.results[name] = EngineResult(name, Verdict.ERROR, error=str(ex))
            file.pending.remove(name)
            return
        except OSError:
            # The worker died while idle (or the file cannot be opened again):
            # the engine is asked again with a new worker
            self._kill(worker)
            self._restart(worker)
            return
        file.pending.remove(name)
        worker.state = _State.BUSY
        worker.task = task

    def _done(self, file: _File) -> bool:
        """Every engine has answered for the file (none pending or running)."""
        return not file.pending and len(file.results) == len(self.engines)

    def _wait(self, root: Path) -> None:
        active = [w for w in self._workers if w.state in (_State.STARTING, _State.BUSY)]
        if not active:
            return
        timeout = max(0.0, min(self._deadline(w) for w in active) - time.monotonic())
        ready = multiprocessing.connection.wait([w.conn for w in active], timeout)
        for conn in ready:
            worker = next(w for w in active if w.conn is conn)
            self._handle(worker)
        now = time.monotonic()
        for worker in active:
            if worker.state in (_State.STARTING, _State.BUSY) and now >= self._deadline(worker):
                self._expire(worker)

    def _deadline(self, worker: _Worker) -> float:
        if worker.state is _State.STARTING:
            return worker.started + self.start_timeout
        if worker.task is None:
            return float("inf")
        return worker.task.deadline

    def _handle(self, worker: _Worker) -> None:
        if worker.state is _State.STARTING:
            error = self._receive_ready(worker, {})
            if error is not None:
                log_event(logger, "worker_load_failed", logging.ERROR, error=error)
                self._kill(worker)
                worker.state = _State.FAILED
            return
        task = worker.task
        try:
            message = self._receive(worker)
            if task is None or message.get("task") != task.task_id:
                raise ProtocolError("message for another task")
            if message["type"] != "result":
                raise ProtocolError(f"unexpected message: {message['type'][:100]}")
            result = decode_engine_result(message.get("result"), task.engine)
            if self._kind(task.engine) is EngineKind.CONTENT and task.file.mime is None:
                task.file.mime, task.file.description = decode_file_type(message)
        except (EOFError, OSError):
            self._crashed(worker)
            return
        except ProtocolError as ex:
            self._crashed(worker, f"invalid message ({ex})")
            return
        elapsed = time.monotonic() - task.sent
        if elapsed > self._timeout(task.engine) and result.verdict in (
            Verdict.CLEAN,
            Verdict.SKIPPED,
        ):
            # A late clean answer is not trusted (the kiosk clock, not the worker's)
            result = EngineResult(
                task.engine,
                Verdict.ERROR,
                error=f"timeout ({self._timeout(task.engine):g}s) exceeded",
                duration=elapsed,
            )
        task.file.results[task.engine] = result
        worker.task = None
        worker.state = _State.IDLE

    def _expire(self, worker: _Worker) -> None:
        task = worker.task
        if worker.state is _State.STARTING or task is None:
            log_event(logger, "worker_start_timeout", logging.ERROR)
            self._kill(worker)
            worker.state = _State.FAILED
            return
        if time.monotonic() >= task.file.deadline:
            reason = f"file scan timeout ({self.file_timeout:g}s, scan.file_timeout)"
        else:
            reason = "engine timeout"
        log_event(
            logger,
            "worker_killed",
            logging.ERROR,
            path=escape(task.file.entry.rel_path),
            engine=task.engine,
            reason=reason,
        )
        self._kill(worker)
        task.file.results[task.engine] = EngineResult(task.engine, Verdict.ERROR, error=reason)
        self._restart(worker)

    def _crashed(self, worker: _Worker, error: str = "") -> None:
        task = worker.task
        if error:
            # A worker sending malformed messages may be compromised: killed
            self._kill(worker)
            reason = f"scan worker killed: {error}"
        else:
            reason = f"scan worker crashed (exit code {self._exit(worker)})"
        log_event(logger, "worker_crashed", logging.ERROR, engine=worker.name, reason=reason)
        self._kill(worker)
        if task is not None:
            task.file.results[task.engine] = EngineResult(task.engine, Verdict.ERROR, error=reason)
        self._restart(worker)

    def _finish(self, root: Path, file: _File) -> FileResult:
        """Combine the results of the engines, in this process."""
        results = [file.results[e.name] for e in self.engines if e.name in file.results]
        content = {e.name for e in self.engines if e.kind is EngineKind.CONTENT}
        verdict = aggregate_file(results, content, self.options.min_malicious_engines)
        return FileResult(
            path=root / file.entry.rel_path,
            size=file.entry.size,
            verdict=verdict,
            results=tuple(results),
            detail=file_detail(verdict, results),
            duration=time.monotonic() - file.start,
            info=dataclasses.replace(file.info, mime=file.mime, description=file.description),
            rel_path=file.entry.rel_path,
        )

    def _restart(self, worker: _Worker) -> None:
        self.restarts += 1
        replacement = self._spawn(worker.spec, worker.index)
        worker.process = replacement.process
        worker.conn = replacement.conn
        worker.state = _State.STARTING
        worker.started = replacement.started
        worker.task = None

    @staticmethod
    def _kill(worker: _Worker) -> None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            if worker.process.poll() is None:
                worker.process.kill()
            worker.process.wait(timeout=5)
        with contextlib.suppress(OSError):
            worker.conn.close()

    @staticmethod
    def _exit(worker: _Worker) -> int | None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            worker.process.wait(timeout=1)
        return worker.process.returncode

    @staticmethod
    def _error_result(root: Path, entry: Entry, reason: str) -> FileResult:
        return FileResult(
            path=root / entry.rel_path,
            size=entry.size,
            verdict=Verdict.ERROR,
            detail=reason,
            rel_path=entry.rel_path,
        )
