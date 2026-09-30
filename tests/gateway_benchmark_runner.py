import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'target/benchmark-upstream/release' / ('monoize-benchmark-upstream.exe' if os.name == 'nt' else 'monoize-benchmark-upstream')
spec = importlib.util.spec_from_file_location("gateway_benchmark", ROOT / "scripts/gateway_benchmark.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def frame(value):
    return ("data: " + (value if isinstance(value, str) else json.dumps(value)) + "\n\n").encode()


def completed_stream():
    return frame({"choices": [{"finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 10, "completion_tokens": 200}}) + frame("[DONE]")


class EvidenceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows timer API')
    def test_timer_period_is_released_after_failure(self):
        clock = Mock()
        clock.timeBeginPeriod.return_value = 0
        with patch.object(runner.ctypes, 'WinDLL', return_value=clock):
            with self.assertRaisesRegex(ValueError, 'fixture failed'):
                with runner.timer_resolution() as precise:
                    self.assertTrue(precise)
                    raise ValueError('fixture failed')
        clock.timeBeginPeriod.assert_called_once_with(1)
        clock.timeEndPeriod.assert_called_once_with(1)

    def test_success_requires_terminal_usage_and_done(self):
        self.assertEqual(runner.classify_response(200, completed_stream()), "served")
        for body in [b"", frame("[DONE]"), completed_stream()[:-2],
                     frame({"choices": [{"finish_reason": "stop"}]}) + frame("[DONE]"),
                     completed_stream().replace(b'200}', b'20}'),
                     frame({"error": {"message": "failed"}}) + completed_stream(),
                     completed_stream() + frame({"choices": []})]:
            with self.subTest(body=body):
                self.assertEqual(runner.classify_response(200, body), "other_errors")

    def test_saturation_requires_the_exact_status_and_error_code(self):
        body = json.dumps({"error": {"code": "gateway_saturated"}}).encode()
        self.assertEqual(runner.classify_response(503, body), "gateway_saturated")
        self.assertEqual(runner.classify_response(500, body), "http_5xx")
        self.assertEqual(runner.classify_response(503, b"unavailable"), "http_5xx")
        self.assertEqual(runner.classify_response(403, body), "other_errors")

    def test_explicit_error_events_cannot_be_hidden_by_a_later_success(self):
        for prefix in [b'event: error\ndata: {"message":"failed"}\n\n',
                       frame({"type": "error", "message": "failed"})]:
            self.assertEqual(runner.classify_response(200, prefix + completed_stream()), "other_errors")

    def test_empty_samples_are_not_zero_latency(self):
        self.assertIsNone(runner.percentile([], .99))
        self.assertTrue(all(value is None for value in runner.distribution([]).values()))

    def test_conservation_and_exact_saturation_boundary(self):
        step = {"scheduled": 2000, "dispatched": 2000, "served": 1999,
                "gateway_saturated": 1, "http_5xx": 0, "other_errors": 0,
                "dispatch_lateness_ms": {"max": 0},
                "gateway_overhead_ms": {"p50": 50, "p90": 150, "p99": 500, "max": 2000},
                "ttfb_ms": {"p50": 350, "p99": 800}, "upstream_ms": {"p50": 1300, "p99": 1300}}
        self.assertEqual(runner.step_failures(step), [])
        step.update(served=1998, gateway_saturated=2)
        self.assertIn("saturation_budget_exceeded", runner.step_failures(step))
        step["dispatched"] = 1999
        self.assertIn("offered_load_missing", runner.step_failures(step))
        self.assertIn("request_conservation_failed", runner.step_failures(step))
        step['upstream_ms']['p99'] = 1351
        self.assertIn("upstream_timing_invalid", runner.step_failures(step))

    def test_child_cpu_sampling_is_available(self):
        if os.name == "nt" or runner.sys.platform.startswith("linux"):
            self.assertGreaterEqual(runner.process_cpu_seconds(os.getpid()), 0)

    def test_process_environment_excludes_existing_deployment_settings(self):
        parent = ROOT / "local-test"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            with patch.dict(os.environ, {
                "MONOIZE_DATABASE_DSN": "postgres://existing/database",
                "DATABASE_URL": "sqlite://existing.db",
                "MONOIZE_UPSTREAM_PROXY_URL": "http://existing:8080",
                "MONOIZE_NODE_ROLE": "replica",
            }):
                environment = runner.isolated_environment(Path(directory))
            self.assertFalse(any(key.startswith("MONOIZE_") for key in environment))
            self.assertNotIn("DATABASE_URL", environment)
            self.assertTrue(Path(environment["TEMP"]).is_relative_to(Path(directory)))


class HttpEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_idle_connection_does_not_fail_the_next_request(self):
        old_reader = asyncio.StreamReader()
        old_reader.feed_eof()
        old_writer = Mock()
        old_writer.wait_closed = AsyncMock(side_effect=ConnectionResetError('expired'))
        fresh_reader = asyncio.StreamReader()
        fresh_writer = Mock()
        fresh_writer.wait_closed = AsyncMock()
        pool = runner.HttpPool(1)
        pool.idle.append((old_reader, old_writer))
        self.addAsyncCleanup(pool.close)
        with patch.object(runner.asyncio, 'open_connection', AsyncMock(return_value=(fresh_reader, fresh_writer))):
            self.assertEqual(await pool.acquire(), (fresh_reader, fresh_writer))

    async def test_cancellation_stops_dispatch_and_cancels_requests(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        calls = []

        async def request(*args, **kwargs):
            calls.append(None)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(runner, 'http_request', side_effect=request):
            task = asyncio.create_task(runner.run_step(1, None, 600, 60, None))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            await asyncio.wait_for(cancelled.wait(), 1)
            await asyncio.sleep(.15)
            self.assertEqual(len(calls), 1)

    @unittest.skipUnless(NATIVE.is_file(), 'build the native benchmark upstream first')
    async def test_native_upstream_preserves_terminal_contract_and_timings(self):
        parent = ROOT / "local-test"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            directory = Path(temporary)
            upstream = runner.ProcessUpstream(NATIVE)
            try:
                port = await upstream.start(directory, runner.isolated_environment(directory))
                result = await runner.run_calibration(port, 240, 1, upstream, directory, runner.isolated_environment(directory))
                self.assertEqual(result['served'], 4)
                self.assertEqual(result['other_errors'], 0)
                self.assertGreaterEqual(result['upstream_ms']['p50'], 1290)
                status, body, _, _ = await runner.http_request(port, '/v1/chat/completions', {
                    'messages': [{'role': 'user', 'content': 'not-a-benchmark'}],
                })
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body)['error']['code'], 'invalid_benchmark_request')
            finally:
                await upstream.close()

    async def test_calibration_merges_raw_worker_samples_and_counts(self):
        parent = ROOT / "local-test"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            directory = Path(temporary)
            upstream = runner.ProcessUpstream()
            environment = runner.isolated_environment(directory)
            try:
                port = await upstream.start(directory, environment)
                result = await runner.run_calibration(port, 240, 1, upstream, directory, environment)
                self.assertEqual(result['scheduled'], 4)
                self.assertEqual(result['dispatched'], 4)
                self.assertEqual(result['served'], 4)
                self.assertEqual(result['other_errors'], 0)
                self.assertGreaterEqual(result['upstream_ms']['p50'], 1290)
                self.assertGreaterEqual(result['load_generator_cpu_seconds'], 0)
                self.assertEqual(upstream.errors, 0)
            finally:
                await upstream.close()

    async def test_partial_request_disconnect_is_a_mock_error(self):
        observed = asyncio.Event()
        upstream = runner.MockUpstream(publish=lambda event: observed.set() if event['event'] == 'error' else None)
        server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        _, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        writer.write(b'P')
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(observed.wait(), 1)
        self.assertEqual(upstream.errors, 1)

    async def test_idle_expiry_is_not_a_failed_upstream_request(self):
        upstream = runner.MockUpstream(idle_timeout=.01)
        server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
            self.assertEqual(upstream.errors, 0)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_separate_mock_process_transfers_matching_timing(self):
        parent = ROOT / "local-test"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            directory = Path(temporary)
            upstream = runner.ProcessUpstream()
            try:
                port = await upstream.start(directory, runner.isolated_environment(directory))
                self.assertNotEqual(upstream.process.pid, os.getpid())
                status, body, _, duration = await runner.http_request(port, "/v1/chat/completions", {
                    "model": runner.MODEL, "messages": [{"role": "user", "content": "benchmark-process"}],
                })
                self.assertEqual(runner.classify_response(status, body), "served")
                observed = await upstream.take_observation("benchmark-process")
                self.assertGreaterEqual(observed, 1290)
                self.assertLessEqual(observed, duration)
                self.assertEqual(upstream.errors, 0)
            finally:
                await upstream.close()

    async def test_complete_streams_reuse_one_http_connection(self):
        upstream = runner.MockUpstream()
        server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        port = server.sockets[0].getsockname()[1]
        pool = runner.HttpPool(port)
        self.addAsyncCleanup(pool.close)
        for index in range(2):
            status, body, _, _ = await runner.http_request(port, "/v1/chat/completions", {
                "model": runner.MODEL, "messages": [{"role": "user", "content": f"benchmark-reuse-{index}"}],
            }, pool=pool)
            self.assertEqual(runner.classify_response(status, body), "served")
        self.assertEqual(upstream.connections, 1)
        self.assertEqual(upstream.errors, 0)

    async def test_real_mock_stream_has_measured_duration_and_exact_usage(self):
        upstream = runner.MockUpstream()
        server = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        port = server.sockets[0].getsockname()[1]
        status, body, first, duration = await runner.http_request(port, "/v1/chat/completions", {
            "model": runner.MODEL, "messages": [{"role": "user", "content": "benchmark-test"}],
        })
        self.assertEqual(runner.classify_response(status, body), "served")
        self.assertGreaterEqual(first, 290)
        self.assertGreaterEqual(duration, 1290)
        self.assertGreaterEqual(upstream.observations["benchmark-test"], 1290)
        self.assertEqual(upstream.errors, 0)

    async def test_truncated_chunk_is_not_accepted(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"6\r\nabc")
        reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await runner.read_body(reader, {"transfer-encoding": "chunked"})

    async def test_over_limit_chunks_are_rejected_before_allocation(self):
        reader = asyncio.StreamReader()
        reader.feed_data(f"{runner.MAX_BODY + 1:x}\r\n".encode())
        reader.feed_eof()
        with self.assertRaises(ValueError):
            await runner.read_body(reader, {"transfer-encoding": "chunked"})


if __name__ == "__main__":
    unittest.main()
