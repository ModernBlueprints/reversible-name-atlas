"""Idempotent packaged-app ownership of the outbound Foldweave companion."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from name_atlas.foldweave_companion_client import (
    CompanionPairingStateStore,
    CompanionRuntimeLock,
    CompanionTransportError,
)

DEFAULT_INITIAL_RETRY_SECONDS = 0.5
DEFAULT_MAXIMUM_RETRY_SECONDS = 30.0


class PairingConnectionState(StrEnum):
    """Locally observed companion runtime state."""

    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


class ManagedCompanionRuntime(Protocol):
    """One outbound runtime over the existing Foldweave engine."""

    async def __call__(self, state_store: CompanionPairingStateStore) -> None: ...


@runtime_checkable
class WakeableCompanionRuntime(Protocol):
    """A managed runtime whose internal reconnect wait can be interrupted."""

    def wake(self) -> None: ...


RetryWaiter = Callable[[asyncio.Event, float], Awaitable[bool]]


async def _wait_for_wake(wake: asyncio.Event, delay_seconds: float) -> bool:
    """Wait for a lifecycle event or one bounded retry delay."""

    if wake.is_set():
        return True
    try:
        await asyncio.wait_for(wake.wait(), timeout=delay_seconds)
    except TimeoutError:
        return False
    return True


@dataclass(slots=True)
class FoldweaveCompanionSupervisor:
    """Own at most one companion task for the packaged application lifespan."""

    state_store: CompanionPairingStateStore
    runtime: ManagedCompanionRuntime
    initial_retry_seconds: float = DEFAULT_INITIAL_RETRY_SECONDS
    maximum_retry_seconds: float = DEFAULT_MAXIMUM_RETRY_SECONDS
    retry_waiter: RetryWaiter = field(default=_wait_for_wake, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _controller: asyncio.Task[None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _ownership: CompanionRuntimeLock | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _application_running: bool = field(default=False, init=False, repr=False)
    _generation: int = field(default=0, init=False, repr=False)
    _connection_state: PairingConnectionState = field(
        default=PairingConnectionState.DISCONNECTED,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.initial_retry_seconds <= 0:
            raise ValueError("Companion initial retry delay must be positive.")
        if self.maximum_retry_seconds < self.initial_retry_seconds:
            raise ValueError(
                "Companion maximum retry delay cannot be below its initial delay."
            )

    def connection_state(self) -> PairingConnectionState:
        """Return only the exact process-local state the supervisor can prove."""

        return self._connection_state

    async def start(self) -> None:
        """Start app ownership without blocking when no pairing exists."""

        async with self._lock:
            self._application_running = True
            if await self._pairing_is_configured():
                try:
                    await self._acquire_ownership_locked()
                except Exception:
                    self._application_running = False
                    raise
                self._ensure_controller_locked()
            else:
                self._connection_state = PairingConnectionState.DISCONNECTED

    async def pairing_state_changed(self) -> None:
        """Wake or stop the sole runtime after a durable pairing transition."""

        controller_to_stop: asyncio.Task[None] | None = None
        ownership_to_release: CompanionRuntimeLock | None = None
        async with self._lock:
            if not self._application_running:
                return
            if await self._pairing_is_configured():
                await self._acquire_ownership_locked()
                self._ensure_controller_locked()
                self._wake.set()
                if isinstance(self.runtime, WakeableCompanionRuntime):
                    self.runtime.wake()
            else:
                (
                    controller_to_stop,
                    ownership_to_release,
                ) = self._detach_controller_locked()
        try:
            await _cancel_task(controller_to_stop)
        finally:
            _release_ownership(ownership_to_release)

    async def stop_companion(self) -> None:
        """Stop the current relay while leaving the app able to pair again."""

        async with self._lock:
            controller, ownership = self._detach_controller_locked()
        try:
            await _cancel_task(controller)
        finally:
            _release_ownership(ownership)

    async def shutdown(self) -> None:
        """End app ownership and await complete companion cleanup."""

        async with self._lock:
            self._application_running = False
            controller, ownership = self._detach_controller_locked()
        try:
            await _cancel_task(controller)
        finally:
            _release_ownership(ownership)

    async def _acquire_ownership_locked(self) -> None:
        if self._ownership is not None:
            return
        ownership = CompanionRuntimeLock(self.state_store.runtime_lock_path)
        ownership.acquire()
        self._ownership = ownership

    def _ensure_controller_locked(self) -> None:
        controller = self._controller
        if controller is not None and not controller.done():
            return
        self._wake.clear()
        self._generation += 1
        generation = self._generation
        self._connection_state = PairingConnectionState.RECONNECTING
        self._controller = asyncio.create_task(
            self._supervise(generation),
            name="foldweave-companion-supervisor",
        )

    def _detach_controller_locked(
        self,
    ) -> tuple[asyncio.Task[None] | None, CompanionRuntimeLock | None]:
        controller = self._controller
        self._controller = None
        ownership = self._ownership
        self._ownership = None
        self._generation += 1
        self._wake.set()
        self._connection_state = PairingConnectionState.DISCONNECTED
        return controller, ownership

    async def _pairing_is_configured(self) -> bool:
        try:
            await self.state_store.read()
        except (CompanionTransportError, OSError):
            return False
        return True

    async def _supervise(self, generation: int) -> None:
        delay = self.initial_retry_seconds
        try:
            while True:
                if not await self._pairing_is_configured():
                    return
                self._connection_state = PairingConnectionState.RECONNECTING
                try:
                    await self.runtime(self.state_store)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - isolated retry boundary
                    pass
                if not await self._pairing_is_configured():
                    return
                already_woken = self._wake.is_set()
                self._wake.clear()
                woke = already_woken or await self.retry_waiter(self._wake, delay)
                self._wake.clear()
                delay = (
                    self.initial_retry_seconds
                    if woke
                    else min(self.maximum_retry_seconds, delay * 2)
                )
        finally:
            if generation == self._generation:
                self._connection_state = PairingConnectionState.DISCONNECTED


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _release_ownership(ownership: CompanionRuntimeLock | None) -> None:
    if ownership is not None:
        ownership.release()
