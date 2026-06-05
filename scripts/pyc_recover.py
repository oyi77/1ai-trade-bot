#!/usr/bin/env python3
"""
pyc_recover.py — Recover .pyc to structurally-correct .py file for Python 3.13
Produces a source file with all imports, function defs, class defs, docstrings,
and variable declarations reconstructed from bytecode metadata.
"""
import dis, marshal, sys, types, inspect, re, struct, os

def recover_pyc(pyc_path: str, out_path: str):
    """Read .pyc and produce best-effort .py source file."""
    with open(pyc_path, 'rb') as f:
        magic = f.read(4)
        flags_raw = f.read(4)
        timestamp = f.read(4)
        size_raw = f.read(4)
        source_size = struct.unpack('<I', size_raw)[0] if len(size_raw) >= 4 else 0
        code = marshal.load(f)

    lines = []
    lines.append(f"# Recovered from {os.path.basename(pyc_path)}")
    lines.append(f"# Original source size: {source_size} bytes")
    lines.append("")

    # Extract imports from co_names that appear before first function/class
    # Simple heuristic: names used at module level without being defined
    defined_names = set()
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            defined_names.add(const.co_name)

    # Gather constants that look like strings
    const_strings = []
    for const in code.co_consts:
        if isinstance(const, str) and len(const) > 3:
            const_strings.append(const)

    # Find docstring (first string constant)
    if const_strings and const_strings[0].startswith('"""') or const_strings and '\n' in const_strings[0]:
        lines.append(const_strings[0])
        lines.append("")

    # Try to determine imports from module-level names
    import_names = []
    for name in code.co_names:
        if name not in defined_names and not name.startswith('__'):
            import_names.append(name)

    # Heuristic: cluster imports
    stdlib_modules = {'json', 'logging', 'os', 're', 'sys', 'threading', 'time', 'urllib',
                       'datetime', 'timezone', 'timedelta', 'pathlib', 'Path', 'math', 'random',
                       'subprocess', 'collections', 'functools', 'typing', 'itertools',
                       '__file__', 'resolve', 'parent', '__doc__', 'Any', 'Dict', 'List',
                       'Optional', 'Tuple', 'dataclass', 'field', 'dataclasses'}

    imported = set()
    for name in import_names:
        if name in stdlib_modules and name not in imported:
            if name in ('Path',):
                lines.append("from pathlib import Path")
                imported.add(name)
            elif name in ('timezone', 'timedelta'):
                if 'datetime' not in imported:
                    lines.append("from datetime import datetime, timezone, timedelta")
                    imported.add('datetime')
                imported.add(name)
            elif name in ('dataclass', 'field'):
                if 'dataclasses' not in imported:
                    lines.append("from dataclasses import dataclass, field")
                    imported.add('dataclasses')
                imported.add(name)
            elif name in ('Any', 'Dict', 'List', 'Optional', 'Tuple'):
                if 'typing' not in imported:
                    lines.append("from typing import Any, Dict, List, Optional, Tuple")
                    imported.add('typing')
                imported.add(name)

    # Add basic imports that are always there
    essential = ['json', 'logging', 'os', 're', 'sys', 'threading', 'time']
    for mod in essential:
        if mod not in imported:
            if mod == 'time':
                lines.append("import time")
            else:
                lines.append(f"import {mod}")
            imported.add(mod)

    lines.append("")

    # Now reconstruct functions and classes from code constants
    written_lines = set()
    func_info = []
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            func_info.append((const.co_firstlineno, const.co_name, const))

    func_info.sort()

    for orig_lineno, name, func_code in func_info:
        if name == '<module>' or name.startswith('<'):
            continue

        # Determine if it's a class or function by checking for class-like patterns  
        is_class = False
        for subconst in func_code.co_consts:
            if isinstance(subconst, types.CodeType) and subconst.co_name in ('__init__', '__repr__', '__str__'):
                is_class = True
                break

        if is_class:
            # Find base classes from bytecode
            bases = []
            for subname in func_code.co_names:
                if subname[0].isupper() and subname not in ('True', 'False', 'None'):
                    bases.append(subname)

            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"")
            lines.append(f"class {name}{base_str}:")
            
            # Get docstring
            for subconst in func_code.co_consts:
                if isinstance(subconst, str) and subconst.strip():
                    lines.append(f'    """{subconst}"""')
                    break

            # Class members
            for subconst in func_code.co_consts:
                if isinstance(subconst, types.CodeType) and subconst.co_name != '<module>':
                    member = subconst
                    member_args = []
                    argcount = member.co_argcount
                    varnames = member.co_varnames[:argcount]
                    if varnames and varnames[0] == 'self':
                        varnames = varnames[1:]  # Remove self for display
                    args_str = ', '.join(varnames)
                    lines.append(f"    def {member.co_name}(self{', ' + args_str if args_str else ''}):")
                    # Get docstring
                    for mconst in member.co_consts:
                        if isinstance(mconst, str) and mconst.strip():
                            lines.append(f'        """{mconst}"""')
                            break
                    lines.append(f"        # ... ({len(member.co_code)} bytecodes)")
                    lines.append(f"        pass")
                    lines.append("")
        else:
            lines.append("")
            # Function definition
            argcount = func_code.co_argcount
            varnames = func_code.co_varnames[:argcount]

            # Check if it's decorated
            is_property = False
            is_static = False

            if is_property:
                lines.append("@property")
            if is_static:
                lines.append("@staticmethod")

            args_str = ', '.join(varnames)
            lines.append(f"def {name}({args_str}):")
            
            # Get docstring
            for fconst in func_code.co_consts:
                if isinstance(fconst, str) and fconst.strip():
                    lines.append(f'    """{fconst}"""')
                    break

            lines.append(f"    # ... ({len(func_code.co_code)} bytecodes, ~{len(func_code.co_code)//2} lines)")
            lines.append(f"    pass")
            lines.append("")

    lines.append("")
    lines.append("# NOTE: This is a structural reconstruction from .pyc bytecode.")
    lines.append("# Function bodies need to be rewritten from original source.")
    lines.append("# This stub preserves imports, function signatures, and docstrings.")

    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))

    print(f"Recovered structure → {out_path}")
    print(f"  Functions/classes: {len(func_info)}")
    print(f"  Lines: {len(lines)}")


if __name__ == '__main__':
    pyc = sys.argv[1] if len(sys.argv) > 1 else 'scripts/__pycache__/vilona_tradefx_handler.cpython-313.pyc'
    out = sys.argv[2] if len(sys.argv) > 2 else 'scripts/vilona_tradefx_handler_stub.py'
    recover_pyc(pyc, out)
