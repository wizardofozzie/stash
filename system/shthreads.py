# coding=utf-8
"""
Killable threads
"""
import sys
import threading
import ctypes

from .shcommon import M_64


class ShThreadTrace(threading.Thread):
    """ Killable thread implementation with trace """

    def __init__(self, name=None, target=None, args=(), kwargs=None, verbose=None):
        self.killed = False
        super(ShThreadTrace, self).__init__(name=name, target=target,
                                            group=None, args=args, kwargs=kwargs, verbose=verbose)
        self.child_threads = []

    def start(self):
        """Start the thread."""
        self.__run_backup = self.run
        self.run = self.__run  # Force the Thread to install our trace.
        threading.Thread.start(self)

    def __run(self):
        """Hacked run function, which installs the trace."""
        sys.settrace(self.globaltrace)
        self.__run_backup()
        self.run = self.__run_backup

    def globaltrace(self, frame, why, arg):
        return self.localtrace if why == 'call' else None

    def localtrace(self, frame, why, arg):
        if self.killed:
            if why == 'line':
                for ct in self.child_threads:
                    ct.kill()
                raise KeyboardInterrupt()
        return self.localtrace

    def kill(self):
        self.killed = True


class ShThreadCtypes(threading.Thread):
    """
    A thread class that supports raising exception in the thread from
    another thread (with ctypes).
    """

    def __init__(self, name=None, target=None, args=(), kwargs=None, verbose=None):
        self.killed = False
        super(ShThreadCtypes, self).__init__(name=name, target=target,
                                             group=None, args=args, kwargs=kwargs, verbose=verbose)
        self.child_threads = []

    def _async_raise(self):
        tid = self.ident
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid) if M_64 else tid,
                                                         ctypes.py_object(KeyboardInterrupt))
        if res == 0:
            raise ValueError("invalid thread id")
        elif res != 1:
            # "if it returns a number greater than one, you're in trouble,
            # and you should call it again with exc=NULL to revert the effect"
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), 0)
            raise SystemError("PyThreadState_SetAsyncExc failed")

        return res

    def kill(self):
        if not self.killed:
            self.killed = True
            for ct in self.child_threads:
                ct.kill()
            try:
                res = self._async_raise()
            except (ValueError, SystemError):
                self.killed = False
