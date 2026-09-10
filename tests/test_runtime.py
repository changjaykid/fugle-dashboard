"""Unit tests for stock_radar.runtime — single-flight lock + throttled
session, no network."""
import sys
import time
import tempfile
import shutil
import multiprocessing
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.runtime import single_flight_lock, LockBusyError, ThrottledSession


def _lock_worker(lock_path, results, idx):
    """Module-level (not a nested closure) so it is picklable for
    multiprocessing's spawn start method (default on macOS/Python 3.8+)."""
    try:
        with single_flight_lock(lock_path):
            time.sleep(0.2)
            results[idx] = 'acquired'
    except LockBusyError:
        results[idx] = 'busy'


class TestSingleFlightLock(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='radar-lock-test-')
        self.lock_path = Path(self.tmpdir) / 'test.lock'

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lock_can_be_acquired_and_released(self):
        with single_flight_lock(self.lock_path):
            pass  # should not raise
        with single_flight_lock(self.lock_path):
            pass  # released cleanly, reacquirable

    def test_nested_non_blocking_acquire_in_same_process_raises(self):
        """flock is per-fd, not per-process, but opening the file again
        with a fresh fd while the first fd's lock is held must still be
        seen as busy by the OS -- this is exactly the scenario of two
        separate `python3 -m stock_radar.cli` invocations racing."""
        with single_flight_lock(self.lock_path):
            with self.assertRaises(LockBusyError):
                with single_flight_lock(self.lock_path):
                    pass

    def test_lock_released_after_block_allows_reacquire(self):
        with single_flight_lock(self.lock_path):
            pass
        # must not raise -- lock was released when the first `with` exited
        with single_flight_lock(self.lock_path):
            pass

    def test_lock_released_even_if_block_raises(self):
        class Boom(Exception):
            pass
        with self.assertRaises(Boom):
            with single_flight_lock(self.lock_path):
                raise Boom()
        # lock must still be released despite the exception
        with single_flight_lock(self.lock_path):
            pass

    def test_concurrent_processes_only_one_holds_lock_at_a_time(self):
        """Real cross-process test using multiprocessing (separate OS
        processes, not just threads) -- proves this is a real OS-level
        lock, not an in-process-only mutex that a second CLI invocation
        could bypass."""
        manager = multiprocessing.Manager()
        results = manager.dict()
        p1 = multiprocessing.Process(target=_lock_worker, args=(self.lock_path, results, 0))
        p2 = multiprocessing.Process(target=_lock_worker, args=(self.lock_path, results, 1))
        p1.start()
        time.sleep(0.05)  # ensure p1 grabs the lock first
        p2.start()
        p1.join(timeout=5)
        p2.join(timeout=5)
        values = sorted(results.values())
        self.assertEqual(values, ['acquired', 'busy'])


class TestThrottledSession(unittest.TestCase):
    def test_spaces_calls_at_least_min_interval_apart(self):
        clock = {'t': 0.0}
        sleeps = []

        def fake_clock():
            return clock['t']

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock['t'] += seconds

        class FakeReal:
            def get(self, *a, **k):
                return 'response'

        session = ThrottledSession(min_interval_seconds=1.0, real_session=FakeReal(),
                                   sleep=fake_sleep, clock=fake_clock)
        session.get('https://example.com/1')
        clock['t'] += 0.3  # simulate 0.3s of real work between calls
        session.get('https://example.com/2')  # should sleep ~0.7s to reach 1.0s total
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.7, places=6)

    def test_no_sleep_when_enough_time_already_elapsed(self):
        clock = {'t': 0.0}
        sleeps = []

        class FakeReal:
            def get(self, *a, **k):
                return 'response'

        session = ThrottledSession(min_interval_seconds=1.0, real_session=FakeReal(),
                                   sleep=lambda s: sleeps.append(s), clock=lambda: clock['t'])
        session.get('https://example.com/1')
        clock['t'] += 2.0  # plenty of time passed
        session.get('https://example.com/2')
        self.assertEqual(sleeps, [])

    def test_get_delegates_args_and_returns_real_response(self):
        calls = []

        class FakeReal:
            def get(self, *a, **k):
                calls.append((a, k))
                return {'status': 200}

        session = ThrottledSession(min_interval_seconds=0.0, real_session=FakeReal(),
                                   sleep=lambda s: None, clock=lambda: 0.0)
        result = session.get('https://x', headers={'a': 'b'}, timeout=5)
        self.assertEqual(result, {'status': 200})
        self.assertEqual(calls, [(('https://x',), {'headers': {'a': 'b'}, 'timeout': 5})])


if __name__ == '__main__':
    unittest.main()
