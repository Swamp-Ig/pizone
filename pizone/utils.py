"""Utility to add overridable cool-down to a function"""

import time
import inspect
from functools import partial, wraps

class CoolDownDecorator:
    """Decorator object"""
    def __init__(self, func, interval, force='force'):
        self.func = func
        self.signature = inspect.signature(func)
        self.interval = interval
        self.last_run = 0
        self.force = force

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self.func
        return partial(self, obj)

    def __call__(self, *args, **kwargs):
        now = time.time()
        bind = self.signature.bind(*args, **kwargs)
        force = bind.arguments[self.force] if self.force in bind.arguments else False
        if force or (now - self.last_run >= self.interval):
            self.last_run = now
            return self.func(*args, **kwargs)

def CoolDown(interval): #pylint: disable=invalid-name
    """Decorator function to apply cool-down to a function"""
    def apply(func):
        decorator = CoolDownDecorator(func=func, interval=interval)
        return wraps(func)(decorator)
    return apply


class Event:
    """Event object. Add listener to listen."""

    def __init__(self):
        self.handlers = []

    def add(self, handler):
        """Add an event handler.
        Method signature handler(sender, *args, **kwargs)
        Sender is passed from fire.
        """
        self.handlers.append(handler)
        return self

    def remove(self, handler):
        """Remove an event handler"""
        self.handlers.remove(handler)
        return self

    def fire(self, sender, *args, **kwargs):
        """Call associated event handlers with sender and arguments"""
        for handler in self.handlers:
            handler(sender, *args, **kwargs)

    __iadd__ = add
    __isub__ = remove
    __call__ = fire
