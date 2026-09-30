#!/usr/bin/env python3
"""Run an isolated executable benchmark governed by GPB1 through GPB16."""

import argparse
import asyncio
import ctypes
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 1024 * 1024
MODEL = "gpt-5-mini-chat"
RATE_STEPS = (100, 500, 1000, 2000)


@contextmanager
def timer_resolution():
    if os.name != "nt":
        yield False
        return
    clock = ctypes.WinDLL("winmm", winmode=0x800)
    clock.timeBeginPeriod.argtypes = [ctypes.c_uint]
    clock.timeBeginPeriod.restype = ctypes.c_uint
    clock.timeEndPeriod.argtypes = [ctypes.c_uint]
    clock.timeEndPeriod.restype = ctypes.c_uint
    if clock.timeBeginPeriod(1) != 0:
        raise OSError("cannot request benchmark timer precision")
    try:
        yield True
    finally:
        clock.timeEndPeriod(1)


def percentile(values, quantile):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * quantile) - 1)]


def distribution(values):
    return {"p50": percentile(values, .5), "p90": percentile(values, .9),
            "p99": percentile(values, .99), "max": max(values) if values else None}


def classify_response(status, body):
    if status != 200:
        try:
            error = json.loads(body).get("error", {})
        except (ValueError, AttributeError):
            error = {}
        if status == 503 and isinstance(error, dict) and error.get("code") == "gateway_saturated":
            return "gateway_saturated"
        return "http_5xx" if 500 <= status < 600 else "other_errors"
    terminal = False
    usage = False
    done = False
    previous_data = None
    previous_event = None
    try:
        text = body.decode("utf-8").replace("\r\n", "\n")
        if not text.endswith("\n\n"):
            return "other_errors"
        for frame in text.split("\n\n"):
            if any(line.partition(":")[2].strip() == "error" for line in frame.splitlines() if line.startswith("event:")):
                return "other_errors"
            data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
            if not data:
                continue
            if data == "[DONE]":
                if not terminal or not usage or done:
                    return "other_errors"
                done = True
                continue
            if done:
                return "other_errors"
            if data == previous_data:
                event = previous_event
            else:
                event = json.loads(data)
                previous_data, previous_event = data, event
            if not isinstance(event, dict) or event.get("error") is not None or event.get("type") == "error":
                return "other_errors"
            choices = event.get("choices", [])
            for choice in choices:
                reason = choice.get("finish_reason")
                if reason is not None:
                    if reason != "stop":
                        return "other_errors"
                    terminal = True
            if event.get("usage") is not None:
                observed = event["usage"]
                if observed.get("completion_tokens") != 200 or observed.get("prompt_tokens") != 10:
                    return "other_errors"
                usage = True
    except (UnicodeError, ValueError, TypeError, AttributeError):
        return "other_errors"
    return "served" if terminal and usage and done else "other_errors"


async def read_body(reader, headers, first_chunk=None):
    output = bytearray()

    def append(chunk):
        if len(output) + len(chunk) > MAX_BODY:
            raise ValueError("response body limit exceeded")
        if chunk and not output and first_chunk is not None:
            first_chunk()
        output.extend(chunk)

    if headers.get("transfer-encoding", "").lower() == "chunked":
        while True:
            line = await reader.readline()
            if not line:
                raise ValueError("truncated chunk header")
            length = int(line.split(b";", 1)[0].strip(), 16)
            if length < 0 or length > MAX_BODY - len(output):
                raise ValueError("invalid chunk length")
            if length == 0:
                if await reader.readexactly(2) != b"\r\n":
                    raise ValueError("unexpected chunk trailer")
                break
            append(await reader.readexactly(length))
            if await reader.readexactly(2) != b"\r\n":
                raise ValueError("invalid chunk delimiter")
    elif "content-length" in headers:
        remaining = int(headers["content-length"])
        if remaining < 0 or remaining > MAX_BODY:
            raise ValueError("invalid content length")
        while remaining:
            chunk = await reader.read(min(remaining, 65536))
            if not chunk:
                raise ValueError("truncated response body")
            append(chunk)
            remaining -= len(chunk)
    else:
        while chunk := await reader.read(65536):
            append(chunk)
    return bytes(output)


async def headers_from(reader, prefix=b""):
    raw = prefix + await reader.readuntil(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        if line:
            key, separator, value = line.partition(":")
            if not separator:
                raise ValueError("malformed HTTP header")
            headers[key.lower()] = value.strip()
    return lines[0], headers


class HttpPool:
    def __init__(self, port):
        self.port = port
        self.idle = []
        self.writers = set()

    async def acquire(self):
        while self.idle:
            reader, writer = self.idle.pop()
            if not reader.at_eof() and not writer.is_closing():
                return reader, writer
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.writers.discard(writer)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, limit=65536)
        self.writers.add(writer)
        return reader, writer

    async def close(self):
        for writer in self.writers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in self.writers), return_exceptions=True)
        self.writers.clear()
        self.idle.clear()


async def http_request(port, path, payload=None, authorization=None, pool=None):
    started = time.perf_counter()
    first = None

    def on_first():
        nonlocal first
        first = (time.perf_counter() - started) * 1000

    async with asyncio.timeout(30):
        reader, writer = await pool.acquire() if pool else await asyncio.open_connection("127.0.0.1", port, limit=65536)
        reusable = False
        try:
            body = json.dumps(payload).encode() if payload is not None else b""
            method = "POST" if payload is not None else "GET"
            request = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}",
                       "Connection: " + ("keep-alive" if pool else "close"), f"Content-Length: {len(body)}"]
            if payload is not None:
                request.append("Content-Type: application/json")
            if authorization:
                request.append("Authorization: " + authorization)
            writer.write(("\r\n".join(request) + "\r\n\r\n").encode() + body)
            await writer.drain()
            status_line, headers = await headers_from(reader)
            status = int(status_line.split(" ", 2)[1])
            response = await read_body(reader, headers, on_first)
            reusable = (headers.get("connection", "").lower() != "close"
                        and ("content-length" in headers or headers.get("transfer-encoding", "").lower() == "chunked"))
            return status, response, first, (time.perf_counter() - started) * 1000
        finally:
            if pool and reusable and not reader.at_eof():
                pool.idle.append((reader, writer))
            else:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                if pool:
                    pool.writers.discard(writer)


class MockUpstream:
    """Protocol fixture for verifier tests; qualification uses the native helper."""
    def __init__(self, publish=None, idle_timeout=120):
        self.observations = {}
        self.active = set()
        self.errors = 0
        self.connections = 0
        self.publish = publish
        self.idle_timeout = idle_timeout

    async def take_observation(self, marker):
        return self.observations.pop(marker, None)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.active.add(task)
        self.connections += 1
        try:
            while True:
                try:
                    first = await asyncio.wait_for(reader.read(1), self.idle_timeout)
                except asyncio.TimeoutError:
                    break
                if not first:
                    break
                async with asyncio.timeout(30):
                    request, headers = await headers_from(reader, first)
                    await self.respond(request, headers, reader, writer)
                if headers.get("connection", "").lower() == "close":
                    break
        except (OSError, ValueError, KeyError, IndexError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
            self.errors += 1
            if self.publish:
                self.publish({"event": "error", "kind": type(error).__name__})
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.active.discard(task)

    async def respond(self, request, headers, reader, writer):
        if request.split(" ")[:2] != ["POST", "/v1/chat/completions"]:
            raise ValueError("unexpected mock request path")
        payload = json.loads(await read_body(reader, headers))
        marker = payload["messages"][-1]["content"]
        if not isinstance(marker, str) or not marker.startswith("benchmark-"):
            raise ValueError("missing benchmark marker")
        if marker in self.observations:
            raise ValueError("duplicate upstream attempt")
        started = time.perf_counter()
        connection = "close" if headers.get("connection", "").lower() == "close" else "keep-alive"
        writer.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                      f"Transfer-Encoding: chunked\r\nConnection: {connection}\r\n\r\n").encode())

        def encode_frame(value):
            data = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
            frame = ("data: " + data + "\n\n").encode()
            return f"{len(frame):x}\r\n".encode() + frame + b"\r\n"

        async def emit(frame):
            writer.write(frame)
            await writer.drain()

        def event(delta, finish=None):
            return {"id": marker, "object": "chat.completion.chunk", "created": 0,
                    "model": MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        role_frame = encode_frame(event({"role": "assistant"}))
        content_frame = encode_frame(event({"content": " x" * 10}))
        terminal = event({}, "stop")
        terminal["usage"] = {"prompt_tokens": 10, "completion_tokens": 200, "total_tokens": 210}
        terminal_frame = encode_frame(terminal)
        done_frame = encode_frame("[DONE]")
        await asyncio.sleep(max(0, started + .3 - time.perf_counter()))
        await emit(role_frame)
        for index in range(1, 21):
            await asyncio.sleep(max(0, started + .3 + index * .05 - time.perf_counter()))
            await emit(content_frame)
        await emit(terminal_frame)
        await emit(done_frame)
        # Publish before the terminating chunk can wake a client needing its sample.
        self.observations[marker] = (time.perf_counter() - started) * 1000
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        if self.publish:
            self.publish({"event": "timing", "marker": marker,
                          "duration_ms": self.observations[marker]})
            self.observations.pop(marker, None)


async def serve_mock():
    def publish(value):
        print(json.dumps(value, separators=(",", ":")), flush=True)

    upstream = MockUpstream(publish)
    server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0, backlog=4096)
    publish({"event": "ready", "port": server.sockets[0].getsockname()[1]})
    async with server:
        await server.serve_forever()


class ProcessUpstream:
    def __init__(self, binary=None):
        self.binary = binary
        self.observations = {}
        self.waiters = {}
        self.errors = 0
        self.error_samples = []
        self.process = None
        self.collector = None
        self.log = None
        self.closing = False

    @property
    def pids(self):
        return [self.process.pid]

    async def start(self, directory, environment):
        self.log = (directory / "mock.log").open("wb")
        arguments = [str(self.binary)] if self.binary else [sys.executable, str(Path(__file__).resolve()), "--mock-upstream"]
        self.process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=directory, env=environment, stdout=asyncio.subprocess.PIPE, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        ready = json.loads(await asyncio.wait_for(self.process.stdout.readline(), 15))
        if ready.get("event") != "ready" or not isinstance(ready.get("port"), int) or not 0 < ready["port"] < 65536:
            raise RuntimeError("mock upstream handshake failed")
        self.port = ready["port"]
        self.collector = asyncio.create_task(self.collect())
        return self.port

    async def collect(self):
        try:
            while line := await self.process.stdout.readline():
                event = json.loads(line)
                if event["event"] == "timing":
                    marker = event["marker"]
                    duration = event["duration_ms"]
                    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
                        raise ValueError("invalid upstream duration")
                    waiter = self.waiters.pop(marker, None)
                    if waiter is not None and not waiter.done():
                        waiter.set_result(duration)
                    else:
                        self.observations[marker] = duration
                else:
                    self.errors += 1
                    if len(self.error_samples) < 10:
                        self.error_samples.append(event)
            if not self.closing:
                self.errors += 1
        except (ValueError, KeyError):
            self.errors += 1

    async def take_observation(self, marker):
        if marker in self.observations:
            return self.observations.pop(marker)
        waiter = asyncio.get_running_loop().create_future()
        self.waiters[marker] = waiter
        try:
            return await asyncio.wait_for(waiter, 1)
        finally:
            self.waiters.pop(marker, None)

    async def close(self):
        self.closing = True
        if self.collector is not None:
            self.collector.cancel()
            await asyncio.gather(self.collector, return_exceptions=True)
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.log is not None:
            self.log.close()


async def run_step(port, authorization, rpm, seconds, upstream, *, start_at=None, offset=0, raw=False):
    scheduled = rpm * seconds // 60
    counters = {key: 0 for key in ("dispatched", "served", "gateway_saturated", "http_5xx", "other_errors")}
    samples = []
    lateness = []
    error_samples = []
    raw_samples = []
    pool = HttpPool(port)
    started = time.perf_counter() if start_at is None else start_at

    async def request_one(marker, due):
        lateness.append(max(0, time.perf_counter() - due) * 1000)
        counters["dispatched"] += 1
        try:
            status, body, first, duration = await http_request(port, "/v1/chat/completions", {
                "model": MODEL, "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": marker}],
            }, authorization, pool)
            category = classify_response(status, body)
            observed = await upstream.take_observation(marker) if category == "served" and upstream else None
            if category == "served" and (first is None or (upstream is not None and observed is None)):
                category = "other_errors"
            counters[category] += 1
            if category != "served" and len(error_samples) < 10:
                error_samples.append({"category": category, "http_status": status,
                                      "upstream_observed": observed is not None})
            if category == "served":
                samples.append((first, duration - observed if observed is not None else None, observed))
                if raw:
                    raw_samples.append((marker, first, duration))
        except Exception as error:
            counters["other_errors"] += 1
            if len(error_samples) < 10:
                error_samples.append({"category": "other_errors", "error_type": type(error).__name__, "message": str(error)})
            if upstream:
                upstream.observations.pop(marker, None)

    pending = []
    loop = asyncio.get_running_loop()
    scheduled_all = loop.create_future()
    stopped = threading.Event()

    def launch(marker, due):
        if not stopped.is_set():
            pending.append(asyncio.create_task(request_one(marker, due)))

    def finish_scheduling(error=None):
        if not scheduled_all.done():
            if error is None:
                scheduled_all.set_result(None)
            else:
                scheduled_all.set_exception(error)

    def schedule():
        try:
            for index in range(scheduled):
                due = started + offset + index * 60 / rpm
                while (remaining := due - time.perf_counter()) > 0:
                    if stopped.is_set():
                        return
                    time.sleep(min(remaining, .05))
                if stopped.is_set():
                    return
                loop.call_soon_threadsafe(launch, "benchmark-" + uuid.uuid4().hex, due)
            loop.call_soon_threadsafe(finish_scheduling)
        except Exception as error:
            loop.call_soon_threadsafe(finish_scheduling, error)

    scheduler = threading.Thread(target=schedule, name="benchmark-load-scheduler", daemon=True)
    scheduler.start()
    try:
        await scheduled_all
        await asyncio.sleep(max(0, started + seconds - time.perf_counter()))
        await asyncio.gather(*pending)
    finally:
        stopped.set()
        await asyncio.to_thread(scheduler.join)
        unfinished = [task for task in pending if not task.done()]
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)
        await pool.close()
    result = {"rpm": rpm, "seconds": seconds, "scheduled": scheduled, **counters,
            "elapsed_seconds": time.perf_counter() - started, "error_samples": error_samples,
            "dispatch_lateness_ms": distribution(lateness),
            "ttfb_ms": distribution([sample[0] for sample in samples]),
            "gateway_overhead_ms": distribution([sample[1] for sample in samples if sample[1] is not None]),
            "upstream_ms": distribution([sample[2] for sample in samples if sample[2] is not None])}
    if raw:
        result["raw_samples"] = raw_samples
        result["raw_lateness_ms"] = lateness
    return result


async def run_calibration(port, rpm, seconds, upstream, directory, environment):
    processes = []
    logs = []
    paths = []
    try:
        for index in range(4):
            path = directory / f"calibration-client-{index}.json"
            paths.append(path)
            log = (directory / f"calibration-client-{index}.log").open("wb")
            logs.append(log)
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()), "--calibration-worker",
                "--port", str(port), "--rpm", str(rpm // 4), "--seconds", str(seconds),
                "--worker-output", str(path), cwd=directory, env=environment,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(process)
        for process in processes:
            if (await asyncio.wait_for(process.stdout.readline(), 15)).strip() != b"ready":
                raise RuntimeError("calibration client did not become ready")
        start = time.perf_counter() + .5
        for index, process in enumerate(processes):
            process.stdin.write(json.dumps({"start": start, "offset": index * 60 / rpm}).encode() + b"\n")
            await process.stdin.drain()
        exits = await asyncio.gather(*(process.wait() for process in processes))
        if any(exits):
            raise RuntimeError("calibration client failed; inspect its log")
        parts = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        result = {"rpm": rpm, "seconds": seconds, "elapsed_seconds": max(part["elapsed_seconds"] for part in parts)}
        for key in ("scheduled", "dispatched", "served", "gateway_saturated", "http_5xx", "other_errors"):
            result[key] = sum(part[key] for part in parts)
        result["error_samples"] = [error for part in parts for error in part["error_samples"]][:10]
        expected = {sample[0] for part in parts for sample in part["raw_samples"]}
        deadline = time.perf_counter() + 1
        while not expected.issubset(upstream.observations) and time.perf_counter() < deadline:
            await asyncio.sleep(.01)
        samples = []
        for part in parts:
            for marker, first, duration in part["raw_samples"]:
                observed = upstream.observations.pop(marker, None)
                if observed is not None:
                    samples.append((first, duration - observed, observed))
                else:
                    result["served"] -= 1
                    result["other_errors"] += 1
        result["dispatch_lateness_ms"] = distribution([value for part in parts for value in part["raw_lateness_ms"]])
        result["ttfb_ms"] = distribution([sample[0] for sample in samples])
        result["gateway_overhead_ms"] = distribution([sample[1] for sample in samples])
        result["upstream_ms"] = distribution([sample[2] for sample in samples])
        result["load_generator_cpu_seconds"] = sum(part["cpu_seconds"] for part in parts)
        return result
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
                await process.wait()
        for log in logs:
            log.close()


def step_failures(step, *, calibration=False):
    failures = []
    if step["scheduled"] <= 0 or step["dispatched"] != step["scheduled"]:
        failures.append("offered_load_missing")
    accounted = sum(step[key] for key in ("served", "gateway_saturated", "http_5xx", "other_errors"))
    if accounted != step["dispatched"] or step["served"] <= 0:
        failures.append("request_conservation_failed")
    late = step["dispatch_lateness_ms"]["max"]
    if late is None or late > 50:
        failures.append("load_generator_late")
    if step["http_5xx"] or step["other_errors"]:
        failures.append("request_errors")
    upstream = step["upstream_ms"]
    if upstream["p50"] is None or upstream["p50"] < 1290 or upstream["p99"] is None or upstream["p99"] > 1350:
        failures.append("upstream_timing_invalid")
    if calibration:
        if step["gateway_saturated"] or "upstream_timing_invalid" in failures:
            failures.append("upstream_capacity_failed")
        if step["ttfb_ms"]["p50"] is None or step["ttfb_ms"]["p50"] < 290:
            failures.append("upstream_first_frame_early")
        return failures
    if step["gateway_saturated"] * 1000 >= step["scheduled"]:
        failures.append("saturation_budget_exceeded")
    for name, maximum in [("p50", 50), ("p90", 150), ("p99", 500), ("max", 2000)]:
        value = step["gateway_overhead_ms"][name]
        if value is None or value > maximum:
            failures.append("gateway_overhead_" + name)
    for name, maximum in [("p50", 350), ("p99", 800)]:
        value = step["ttfb_ms"][name]
        if value is None or value > maximum:
            failures.append("ttfb_" + name)
    return failures


def process_cpu_seconds(pid):
    if sys.platform == "win32":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise OSError("cannot query benchmark process")
        times = [wintypes.FILETIME() for _ in range(4)]
        try:
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                raise OSError("cannot read benchmark CPU time")
            return sum((value.dwHighDateTime << 32) + value.dwLowDateTime for value in times[2:]) / 10_000_000
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    raise OSError("CPU sampling requires Windows or Linux")


def isolated_environment(directory):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("MONOIZE_") and key not in ("DATABASE_URL", "RUST_LOG")}
    scratch = directory / "scratch"
    scratch.mkdir(exist_ok=True)
    environment.update({"TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
                        "CARGO_HOME": str(ROOT / ".worktrees/cargo-home"),
                        "BUN_INSTALL_CACHE_DIR": str(ROOT / ".worktrees/bun-cache")})
    clang = ROOT / ".worktrees/libclang/clang/native"
    if clang.is_dir() and "LIBCLANG_PATH" not in environment:
        environment["LIBCLANG_PATH"] = str(clang)
    return environment


async def command(arguments, environment, log):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    print(f"running {arguments[0]} {arguments[1]}; log: {log.relative_to(ROOT)}", file=sys.stderr)
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(*arguments, cwd=ROOT, env=environment,
            stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
        if await process.wait():
            raise RuntimeError(f"command failed; inspect {log.relative_to(ROOT)}")


async def benchmark(args, directory, report):
    environment = isolated_environment(directory)
    executable = Path(args.binary).resolve() if args.binary else ROOT / "target/release" / ("monoize.exe" if os.name == "nt" else "monoize")
    if args.profile == "qualification" and args.binary:
        raise ValueError("qualification builds its own release executable")
    if not args.binary:
        await command(["cargo", "build", "--locked", "--release", "--bin", "monoize", "-j", "2"],
                      environment, directory / "build.log")
    await command(["cargo", "build", "--locked", "--release", "--manifest-path",
                   str(ROOT / "tools/gateway-benchmark-upstream/Cargo.toml"), "--target-dir",
                   str(ROOT / "target/benchmark-upstream"), "-j", "2"],
                  environment, directory / "mock-build.log")
    mock_binary = ROOT / "target/benchmark-upstream/release" / ("monoize-benchmark-upstream.exe" if os.name == "nt" else "monoize-benchmark-upstream")
    report["mock_binary_sha256"] = hashlib.sha256(mock_binary.read_bytes()).hexdigest()
    report["binary_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
    report["release_built"] = not bool(args.binary)
    upstream = ProcessUpstream(mock_binary)
    child = None
    monitor_task = None
    log = None
    try:
        upstream_port = await upstream.start(directory, environment)
        report["mock_pids"] = upstream.pids
        rows = 500000 if args.profile == "qualification" else 1000
        environment.update({"GATEWAY_BENCH_DIRECTORY": str(directory),
                            "GATEWAY_BENCH_UPSTREAM_URL": f"http://127.0.0.1:{upstream_port}",
                            "GATEWAY_BENCH_SEED_ROWS": str(rows)})
        await command(["cargo", "test", "--locked", "--test", "gateway_benchmark", "-j", "2", "--",
                       "prepare_process_benchmark_fixture", "--exact", "--ignored", "--nocapture"],
                      environment, directory / "fixture.log")
        fixture = json.loads((directory / "fixture.json").read_text())
        database = directory / "gateway.db"
        with sqlite3.connect(database) as connection:
            count = connection.execute("SELECT count(*) FROM request_logs WHERE api_key_id = ?", (fixture["api_key_id"],)).fetchone()[0]
            finite = connection.execute("SELECT spend_limit_total_nano_usd FROM api_keys WHERE id = ?", (fixture["api_key_id"],)).fetchone()[0]
            if count != rows or finite is None:
                raise ValueError("benchmark fixture does not exercise finite spending limits")
            connection.execute("DELETE FROM store_primary_leases")
            connection.execute("UPDATE monoize_providers SET active_probe_enabled_override = 0, channel_active_probe_enabled_override = 0")
        report["fixture"] = {"history_rows": count, "concentrated_key_rows": count,
                             "distinct_request_keys": 1, "finite_spend_limit": True}
        calibration_seconds = 60 if args.profile == "qualification" else 2
        calibration_rpm = 22000 if args.profile == "qualification" else 120
        print(f"calibrating mock upstream at {calibration_rpm} RPM", file=sys.stderr)
        calibration_cpu = {pid: process_cpu_seconds(pid) for pid in [os.getpid(), *upstream.pids]}
        report["calibration"] = await run_calibration(upstream_port, calibration_rpm, calibration_seconds, upstream, directory, environment)
        report["calibration"]["cpu_seconds"] = {
            "client": report["calibration"]["load_generator_cpu_seconds"],
            "coordinator": process_cpu_seconds(os.getpid()) - calibration_cpu[os.getpid()],
            "upstream": sum(process_cpu_seconds(pid) - calibration_cpu[pid] for pid in upstream.pids),
        }
        report["calibration"]["mock_errors"] = upstream.errors
        report["calibration"]["mock_error_samples"] = upstream.error_samples
        failures = step_failures(report["calibration"], calibration=True)
        if failures:
            report["failures"].extend("calibration:" + item for item in failures)
            return
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        environment.update({"MONOIZE_DATABASE_DSN": "sqlite://" + database.as_posix(),
            "MONOIZE_LISTEN": f"127.0.0.1:{port}", "MONOIZE_NODE_ROLE": "primary",
            "MONOIZE_ALLOW_PRIVATE_UPSTREAM": "1", "MONOIZE_REQUEST_LOG_SPOOL_DIR": str(directory / "spool"),
            "MONOIZE_TOKIO_WORKER_THREADS": "6", "MONOIZE_FORWARD_INFLIGHT_LIMIT": "0",
            "RUST_LOG": "warn,sqlx::pool=warn"})
        log = (directory / "gateway.log").open("wb")
        child = await asyncio.create_subprocess_exec(str(executable), cwd=directory, env=environment,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        report["gateway_pid"] = child.pid
        deadline = time.perf_counter() + 90
        while True:
            if child.returncode is not None or time.perf_counter() > deadline:
                raise RuntimeError("benchmark gateway did not become ready")
            try:
                if (await http_request(port, "/readyz"))[0] == 200:
                    break
            except (OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(.25)

        resources = []
        started = time.perf_counter()
        active_rpm = 0

        async def monitor():
            previous_time = time.perf_counter()
            previous_cpu = process_cpu_seconds(child.pid)
            while True:
                await asyncio.sleep(1)
                now = time.perf_counter()
                cpu = process_cpu_seconds(child.pid)
                wal = Path(str(database) + "-wal")
                resources.append({"seconds": now - started, "rpm": active_rpm,
                    "cpu_percent_of_workers": (cpu - previous_cpu) / (now - previous_time) / 6 * 100,
                    "wal_bytes": wal.stat().st_size if wal.exists() else 0})
                previous_time, previous_cpu = now, cpu

        monitor_task = asyncio.create_task(monitor())
        report["steps"] = []
        slow_acquire_offset = 0
        for active_rpm in RATE_STEPS:
            if active_rpm == 2000:
                slow_acquire_offset = (directory / "gateway.log").stat().st_size
            seconds = 60 if args.profile == "qualification" else 2
            step = await run_step(port, fixture["auth_header"], active_rpm, seconds, upstream)
            step["failures"] = step_failures(step)
            report["steps"].append(step)
            report["failures"].extend(f"{active_rpm}rpm:{item}" for item in step["failures"])
            print(f"completed {active_rpm} RPM: {step['served']}/{step['scheduled']} successful", file=sys.stderr)
        if args.profile == "qualification":
            while time.perf_counter() - started < 601:
                step = await run_step(port, fixture["auth_header"], 2000, 60, upstream)
                step["failures"] = step_failures(step)
                report.setdefault("steady_steps", []).append(step)
                report["failures"].extend("steady:" + item for item in step["failures"])
                print(f"completed steady 2000 RPM: {step['served']}/{step['scheduled']} successful", file=sys.stderr)
        if monitor_task.done():
            monitor_task.result()
        report["resources"] = resources
        peak_samples = [sample for sample in resources if sample["rpm"] == 2000]
        if not peak_samples or max(sample["cpu_percent_of_workers"] for sample in peak_samples) >= 70:
            report["failures"].append("CPU_budget_or_evidence")
        wal_samples = [sample for sample in resources if sample["seconds"] >= 600]
        if args.profile == "qualification" and (not wal_samples or max(sample["wal_bytes"] for sample in wal_samples) > 268435456):
            report["failures"].append("WAL_budget_or_evidence")
        log.flush()
        with (directory / "gateway.log").open("rb") as gateway_log:
            gateway_log.seek(slow_acquire_offset)
            lines = gateway_log.read().decode("utf-8", errors="replace").splitlines()
        slow = sum("sqlx::pool" in line and ("slow" in line.lower() or "exceeded" in line.lower()) for line in lines)
        report["slow_acquire_warnings"] = slow
        if slow:
            report["failures"].append("slow_connection_acquire")
        report["mock_upstream_errors"] = upstream.errors
        if upstream.errors:
            report["failures"].append("mock_upstream_errors")
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if child is not None and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 30)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        if log is not None:
            log.close()
        await upstream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("smoke", "qualification"), default="smoke")
    parser.add_argument("--binary", help="existing executable for smoke only")
    parser.add_argument("--output", type=Path, help="new report directory inside the repository")
    parser.add_argument("--mock-upstream", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--calibration-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--rpm", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--seconds", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.calibration_worker:
        path = args.worker_output.resolve()
        if not path.is_relative_to(ROOT) or not 0 < args.port < 65536 or args.rpm <= 0 or args.seconds <= 0:
            parser.error("invalid calibration worker parameters")
        with timer_resolution() as precise_timer:
            print("ready", flush=True)
            schedule = json.loads(sys.stdin.buffer.readline())
            before = time.process_time()
            result = asyncio.run(run_step(args.port, None, args.rpm, args.seconds, None,
                                start_at=schedule["start"], offset=schedule["offset"], raw=True))
            result["cpu_seconds"] = time.process_time() - before
            result["windows_timer_period_ms"] = 1 if precise_timer else None
        with path.open("x", encoding="utf-8") as output:
            json.dump(result, output)
        return 0
    if args.mock_upstream:
        asyncio.run(serve_mock())
        return 0
    directory = (args.output or ROOT / "local-test" / ("gateway-benchmark-" + uuid.uuid4().hex)).resolve()
    if not directory.is_relative_to(ROOT) or directory == ROOT:
        parser.error("output must be a new directory inside the repository")
    directory.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "profile": args.profile, "host": socket.gethostname(),
              "platform": platform.platform(), "failures": [], "qualification_passed": False,
              "production_capacity_verified": False, "worker_threads": 6,
              "wal_limit_bytes": 268435456}
    source = Path(__file__).read_bytes()
    (directory / "runner-source.py").write_bytes(source)
    report["runner_sha256"] = hashlib.sha256(source).hexdigest()
    fixture_source = (ROOT / "tests/gateway_benchmark.rs").read_bytes()
    (directory / "fixture-source.rs").write_bytes(fixture_source)
    report["fixture_source_sha256"] = hashlib.sha256(fixture_source).hexdigest()
    mock_source = (ROOT / "tools/gateway-benchmark-upstream/src/main.rs").read_bytes()
    (directory / "mock-source.rs").write_bytes(mock_source)
    report["mock_source_sha256"] = hashlib.sha256(mock_source).hexdigest()
    mock_lock = (ROOT / "tools/gateway-benchmark-upstream/Cargo.lock").read_bytes()
    (directory / "mock-Cargo.lock").write_bytes(mock_lock)
    report["mock_lock_sha256"] = hashlib.sha256(mock_lock).hexdigest()
    try:
        report["git_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        report["working_diff_sha256"] = hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)).hexdigest()
        with timer_resolution() as precise_timer:
            report["windows_timer_period_ms"] = 1 if precise_timer else None
            asyncio.run(benchmark(args, directory, report))
    except (Exception, KeyboardInterrupt) as error:
        report["failures"].append(f"runner:{type(error).__name__}:{error}")
    report["qualification_passed"] = args.profile == "qualification" and not report["failures"]
    report["smoke_passed"] = args.profile == "smoke" and not report["failures"]
    text = json.dumps(report, indent=2, allow_nan=False)
    (directory / "report.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"report: {directory / 'report.json'}", file=sys.stderr)
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
