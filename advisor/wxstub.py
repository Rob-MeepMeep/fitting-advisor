"""Permissive import stub for wxPython so Pyfa's eos engine runs headlessly.

Must be installed via ``install()`` before importing anything from Pyfa.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types


class _Any:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Any()

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def __mro_entries__(self, bases):
        return (object,)

    def __or__(self, other):
        return self

    __ror__ = __or__


class _Mod(types.ModuleType):
    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        if name in ("NewEvent", "NewCommandEvent"):
            return lambda: (_Any, _Any())
        return _Any()


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname: str, path, target=None):
        if fullname == "wx" or fullname.startswith("wx."):
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        module = _Mod(spec.name)
        module.__path__ = []
        return module

    def exec_module(self, module):
        pass


def install() -> None:
    """Install the wx import stub onto ``sys.meta_path`` (idempotent)."""
    if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
