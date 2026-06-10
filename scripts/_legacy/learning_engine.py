# Auto-loaded from preserved .pyc binary (safe copy)
import importlib.util, sys, os

_SAFE = '/tmp/learning_engine_safe.pyc'
if os.path.exists(_SAFE) and os.path.getsize(_SAFE) > 5000:
    _spec = importlib.util.spec_from_file_location('learning_engine._impl', os.path.abspath(_SAFE))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    for _k, _v in _mod.__dict__.items():
        if not _k.startswith('_'):
            globals()[_k] = _v
    if hasattr(_mod, '__doc__'):
        globals()['__doc__'] = _mod.__doc__
