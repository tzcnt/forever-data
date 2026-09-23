"""Run the actual addon under Lua 5.1 with mocked WoW APIs. Requires lupa."""
import os
from pathlib import Path

try:
    from lupa.lua51 import LuaRuntime
except ImportError:
    raise SystemExit('Addon replay tests require lupa with Lua 5.1: python -m pip install lupa')

root = Path(__file__).resolve().parents[1]
os.chdir(root)
runtime = LuaRuntime(unpack_returned_tuples=True)
runtime.execute('assert(loadfile("ForeverState.lua"))')
runtime.execute((root / "tests" / "replay.lua").read_text(encoding="utf-8"))
