# The scene, settings and slicing may only be touched from the Qt main thread.
# run_on_main() schedules a function there and waits for its result.

import threading

from steslicer.SteSlicerApplication import SteSlicerApplication


class _Call:
    def __init__(self, func, args):
        self.func = func
        self.args = args
        self.done = threading.Event()
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.func(*self.args)
        except Exception as e:
            self.error = e
        finally:
            self.done.set()


def run_on_main(func, *args, timeout=60):
    call = _Call(func, args)
    if threading.current_thread() is threading.main_thread():
        call.run()
    else:
        SteSlicerApplication.getInstance().callLater(call.run)
        if not call.done.wait(timeout):
            raise TimeoutError("Слайсер не ответил за %d с" % timeout)
    if call.error is not None:
        raise call.error
    return call.result
