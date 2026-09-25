"""Bounded JSONL protocol for process agents. Tool effects remain in the parent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core import AgentResult, Context, ToolClient


class ProtocolError(Exception):
    pass


async def _drain(stream):
    # Drain continuously without retaining arbitrary agent stderr in memory.
    while await stream.read(8192):
        pass


async def exchange(command, user_input, tools: ToolClient, context: Context, env=None):
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        limit=1024 * 1024,
        start_new_session=os.name == "posix",
    )
    drain = asyncio.create_task(_drain(process.stderr))

    async def send(message):
        data = json.dumps(message, allow_nan=False).encode() + b"\n"
        if len(data) > 1024 * 1024:
            raise ProtocolError("Message exceeds one MiB")
        process.stdin.write(data)
        await process.stdin.drain()

    try:
        await send(
            {
                "protocol": "1",
                "input": user_input,
                "context": asdict(context),
                "tools": {name: asdict(spec) for name, spec in tools.specifications.items()},
            }
        )
        seen = set()
        for _ in range(1000):
            try:
                line = await process.stdout.readline()
            except ValueError as exc:
                raise ProtocolError("Agent message exceeds one MiB") from exc
            if not line:
                await process.wait()
                raise ProtocolError(f"Agent exited without a result (status {process.returncode})")
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise ProtocolError("Agent returned invalid JSON") from exc
            if not isinstance(message, dict):
                raise ProtocolError("Agent message must be an object")
            if message.get("method") == "result":
                if not isinstance(message.get("text"), str):
                    raise ProtocolError("Result text must be a string")
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 2)
                if process.returncode != 0:
                    raise ProtocolError("Agent returned a result but exited unsuccessfully")
                return AgentResult(message["text"], message.get("metadata", {}))
            if message.get("method") != "tool.call":
                raise ProtocolError("Unsupported protocol method")
            call_id = message.get("id")
            if type(call_id) is not int or call_id in seen:
                raise ProtocolError("Tool request IDs must be unique integers")
            seen.add(call_id)
            name, arguments = message.get("name"), message.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ProtocolError("Tool name and arguments have invalid types")
            try:
                result = await tools.call(name, **arguments)
                response = {"id": call_id, "result": result}
            except Exception as exc:
                response = {"id": call_id, "error": {"type": type(exc).__name__}}
            await send(response)
        raise ProtocolError("Agent exceeded protocol message limit")
    finally:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.kill()
        await process.wait()
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)


@dataclass
class ProcessDriver:
    """Trusted-code adapter. This is process separation, not a security sandbox."""

    command: tuple[str, ...]
    isolation = "trusted_subprocess"

    def manifest(self):
        return {"command": self.command}

    async def run(self, user_input, tools, context):
        return await exchange(
            self.command,
            user_input,
            tools,
            context,
            env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
        )


@dataclass
class DockerDriver:
    """Offline Linux container. Requires a trusted local image containing Python."""

    script: str
    image: str = "python:3.11-slim"
    isolation = "docker_network_none"

    def manifest(self):
        return {
            "image": self.image,
            "script_sha256": hashlib.sha256(Path(self.script).read_bytes()).hexdigest(),
        }

    async def run(self, user_input, tools, context):
        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError("Docker executable is unavailable; isolation cannot start")
        script = Path(self.script).resolve(strict=True)
        remote = Path(__file__).parents[1] / "remote.py"
        # Pin the running image to the locally resolved immutable image ID.
        inspect = await asyncio.create_subprocess_exec(
            docker,
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            self.image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(inspect.communicate(), 10)
        except BaseException:
            if inspect.returncode is None:
                inspect.kill()
            await inspect.wait()
            raise
        if inspect.returncode:
            raise RuntimeError(
                "Docker daemon or local image unavailable; pull the image explicitly"
            )
        image_id = stdout.decode().strip()
        if not image_id.startswith("sha256:"):
            raise RuntimeError("Could not resolve immutable Docker image ID")
        name = f"agent-eval-{context.run_id}"
        command = (
            docker,
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,src={script},dst=/agent/main.py,readonly",
            "--mount",
            f"type=bind,src={remote},dst=/agent/remote.py,readonly",
            "--workdir",
            "/agent",
            "--entrypoint",
            "/usr/bin/env",
            image_id,
            "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE=1",
            "python",
            "-u",
            "/agent/main.py",
        )
        try:
            result = await exchange(command, user_input, tools, context)
            return AgentResult(result.text, {**result.metadata, "image_id": image_id})
        finally:
            # Killing the Docker CLI alone does not guarantee container termination.
            cleanup = await asyncio.create_subprocess_exec(
                docker,
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, cleanup_error = await asyncio.wait_for(cleanup.communicate(), 10)
                if cleanup.returncode and b"No such container" not in cleanup_error:
                    raise RuntimeError(f"Could not confirm container cleanup: {name}")
            except TimeoutError:
                cleanup.kill()
                await cleanup.wait()
                raise RuntimeError(f"Container cleanup timed out: {name}") from None
