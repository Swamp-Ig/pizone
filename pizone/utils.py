"""Utilities"""

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
