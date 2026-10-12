from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from haoai_backend.media_storage.owned_media_runtime import (
    OwnedMediaDependencies,
    OwnedMediaRuntime,
)


def _make_runtime(directory: Path) -> OwnedMediaRuntime:
    return OwnedMediaRuntime(
        OwnedMediaDependencies(
            transfer_tmp_dir=directory,
            getaddrinfo=None,
            http_library=None,
            media_policy=None,
            r2_configuration=None,
            access_key=None,
            secret_key=None,
            s3_client_factory=None,
        ),
        loop_factory=asyncio.new_event_loop,
        thread_factory=threading.Thread,
        executor_factory=concurrent.futures.ThreadPoolExecutor,
    )


class OwnedMediaRuntimeTests(unittest.TestCase):
    def test_facade_calls_share_one_owned_loop_and_two_actual_slots(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = _make_runtime(Path(raw))
            active = 0
            peak = 0

            async def work(services):
                nonlocal active, peak
                async with services.shared_slots:
                    active += 1
                    peak = max(peak, active)
                    await asyncio.sleep(0.025)
                    active -= 1
                return id(services), runtime.owner_thread_id

            caller_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
            try:
                futures = [caller_pool.submit(runtime.run, work) for _ in range(6)]
                results = [future.result(timeout=5) for future in futures]
                self.assertEqual(len({result[0] for result in results}), 1)
                self.assertEqual(len({result[1] for result in results}), 1)
                self.assertNotEqual(results[0][1], threading.get_ident())
                self.assertEqual(peak, 2)
                self.assertEqual(runtime.state, "running")
            finally:
                runtime.close()
                caller_pool.shutdown(wait=True)
            self.assertEqual(runtime.state, "closed")
            self.assertFalse(
                any(
                    thread.name == "haoai-owned-media-runtime"
                    for thread in threading.enumerate()
                )
            )

    def test_running_loop_call_is_rejected_without_starting_owner(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = _make_runtime(Path(raw))

            async def call_inside_loop() -> None:
                with self.assertRaisesRegex(RuntimeError, "inside an event loop"):
                    runtime.run(lambda _services: asyncio.sleep(0))

            try:
                asyncio.run(call_inside_loop())
                self.assertEqual(runtime.state, "new")
                self.assertIsNone(runtime.owner_thread_id)
            finally:
                runtime.close()

    def test_shutdown_waits_for_active_call_to_settle(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = _make_runtime(Path(raw))
            entered = threading.Event()
            finished = threading.Event()
            result: list[str] = []

            async def work(_services):
                entered.set()
                await asyncio.sleep(0.12)
                return "settled"

            def call() -> None:
                result.append(runtime.run(work))
                finished.set()

            caller = threading.Thread(target=call, name="media-runtime-caller")
            caller.start()
            self.assertTrue(entered.wait(3))
            started_close = time.monotonic()
            runtime.close()
            close_elapsed = time.monotonic() - started_close
            caller.join(timeout=2)

            self.assertFalse(caller.is_alive())
            self.assertTrue(finished.is_set())
            self.assertEqual(result, ["settled"])
            self.assertGreaterEqual(close_elapsed, 0.08)
            self.assertEqual(runtime.state, "closed")


if __name__ == "__main__":
    unittest.main()
