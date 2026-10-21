import asyncio
import concurrent.futures
import threading
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            thread = threading.Thread(target=_run, name="ai-event-loop", daemon=True)
            thread.start()
            ready.wait()
            _loop, _loop_thread = loop, thread
        return _loop


def run_sync(coro, timeout: float = 120.0):
    """Run a coroutine on the ONE shared background event loop and wait for it."""
    future = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise


def shutdown_loop() -> None:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            _loop.call_soon_threadsafe(_loop.stop)
            if _loop_thread is not None:
                _loop_thread.join(timeout=5)
            _loop.close()
        _loop = None
        _loop_thread = None
