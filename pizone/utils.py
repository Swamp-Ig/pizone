import time
import inspect
from functools import partial, wraps

class CoolDownDecorator(object):
  def __init__(self,func,interval,force='force'):
    self.func = func
    self.signature = inspect.signature(func)
    self.interval = interval
    self.last_run = 0
    self.force = force
  def __get__(self,obj,objtype=None):
    if obj is None:
      return self.func
    return partial(self,obj)
  def __call__(self,*args,**kwargs):
    now = time.time()
    bind = self.signature.bind(*args, **kwargs)
    force = bind.arguments[self.force] if self.force in bind.arguments else False
    if force or (now - self.last_run >= self.interval):
      self.last_run = now
      return self.func(*args,**kwargs)

def CoolDown(interval):
  def applyDecorator(func):
    decorator = CoolDownDecorator(func=func,interval=interval)
    return wraps(func)(decorator)
  return applyDecorator
