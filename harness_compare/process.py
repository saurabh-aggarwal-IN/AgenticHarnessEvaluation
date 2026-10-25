from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, limit: int = 4_000) -> str:
        text = (self.stdout + "\n" + self.stderr).strip()
        return text[-limit:]


class CommandTimeout(RuntimeError):
    def __init__(self, message: str, result: CommandResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class CommandError(RuntimeError):
    def __init__(self, message: str, result: CommandResult) -> None:
        super().__init__(message)
        self.result = result


class SubprocessRunner:
    """Async subprocess execution with a hard timeout and child-tree kill."""

    async def run(
        self,
        args: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 300.0,
        stdin_text: str | None = None,
    ) -> CommandResult:
        merged = dict(os.environ)
        if env:
            merged.update(env)
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd) if cwd else None,
                env=merged,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise CommandError(
                f"could not start {args[0]}: {exc}",
                CommandResult(args, 127, "", str(exc), 0),
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(
                    stdin_text.encode() if stdin_text is not None else None
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as exc:
            await self._kill_tree(process)
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=5
                )
            except (asyncio.TimeoutError, ProcessLookupError):
                stdout, stderr = b"", b""
            result = CommandResult(
                args,
                process.returncode if process.returncode is not None else -9,
                stdout.decode("utf-8", "replace"),
                stderr.decode("utf-8", "replace"),
                int((time.monotonic() - started) * 1000),
            )
            raise CommandTimeout(
                f"command timed out after {timeout_s:.0f}s: {args[0]} {args[1] if len(args) > 1 else ''}".strip(),
                result,
            ) from exc

        return CommandResult(
            args,
            process.returncode if process.returncode is not None else -1,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
            int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    async def _kill_tree(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
                process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except ProcessLookupError:
                pass


def write_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")
