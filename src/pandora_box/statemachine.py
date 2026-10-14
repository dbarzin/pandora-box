"""Generic state machine driving the kiosk."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum


class State(StrEnum):
    START = "START"
    WAIT = "WAIT"
    INSERTED = "INSERTED"
    SCAN = "SCAN"
    CLEAN = "CLEAN"
    ERROR = "ERROR"
    STOP = "STOP"


Handler = Callable[[], State]


class StateMachine:
    """Run handlers until the STOP state; each handler returns the next state."""

    def __init__(self, handlers: Mapping[State, Handler]) -> None:
        self.handlers = dict(handlers)
        self.state = State.START

    def step(self) -> State:
        handler = self.handlers.get(self.state)
        self.state = handler() if handler is not None else State.STOP
        return self.state

    def run(self) -> None:
        while self.state is not State.STOP:
            self.step()
