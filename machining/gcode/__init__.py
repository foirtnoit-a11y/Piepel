"""G-code verification toolkit for Fanuc/Haas milling programs.

The OneCNC XR5 post is a black box, so this package treats its output as
untrusted input: parse it, simulate it, and check it against the real machine
and the real setup before anyone presses cycle start.
"""
from .parser import parse, Block, Word, program_number
from .machine import Machine, Setup, Stock, Tool, Envelope
from .interp import run as backplot, Result, Move
from .lint import lint, Diagnostic

__all__ = [
    "parse", "Block", "Word", "program_number",
    "Machine", "Setup", "Stock", "Tool", "Envelope",
    "backplot", "Result", "Move", "lint", "Diagnostic",
]
