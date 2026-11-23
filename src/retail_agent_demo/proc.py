# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The one place this project starts other programs.

Every caller passes an argument list (never a string), nothing goes through a shell, and each argument must be text or a
path. Keeping the two `subprocess` calls here means a code scanner has one spot to review instead of one per script.
The arguments come from the repository (script paths, fixed flags) or from the operator's own configuration; none come
from a request, a model or a web page.

The program itself is checked too: `run` and `popen` start only this Python interpreter and Graphviz `dot`
(which draws the architecture pictures), so a caller cannot be made to start something else. The one command that is the operator's own
choice, the password lookup in `read_command_output`, is the only exception and is read from the operator's local `.env`.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 - imported for constants or in the single audited helper
import sys
from collections.abc import Sequence
from typing import Any

# Programs the repository's own scripts start (besides this Python interpreter). Matched on the file name without a directory or .exe.
ALLOWED_PROGRAMS = frozenset({"dot"})


def _argv(args: Sequence[str | os.PathLike[str]], *, operator_command: bool = False) -> list[str]:
    if isinstance(args, (str, bytes)) or not args:
        raise ValueError("pass a non-empty list of arguments, not a string")
    argv = [os.fspath(a) for a in args]
    if not operator_command:
        program = os.path.basename(argv[0]).lower().removesuffix(".exe")
        if os.path.realpath(argv[0]) != os.path.realpath(sys.executable) and program not in ALLOWED_PROGRAMS:
            raise ValueError(f"{argv[0]!r} is not a program this project starts (allowed: Python, {', '.join(sorted(ALLOWED_PROGRAMS))})")
    return argv


def _no_shell(kwargs: dict[str, Any]) -> None:
    if kwargs.get("shell"):
        raise ValueError("shell=True is not allowed")


def run(args: Sequence[str | os.PathLike[str]], *, _operator_command: bool = False, **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run with an argument list, no shell, and only the programs this project uses."""
    _no_shell(kwargs)
    # Reviewed: _argv() accepts only a list of text or paths and only an allowed program, _no_shell() refuses shell=True, and callers
    # pass repository paths, fixed flags or the operator's own configuration.
    # nosemgrep: dangerous-subprocess-use-audit
    return subprocess.run(_argv(args, operator_command=_operator_command), **kwargs)  # nosec B603


def popen(args: Sequence[str | os.PathLike[str]], **kwargs: Any) -> subprocess.Popen:
    """subprocess.Popen with an argument list and no shell."""
    _no_shell(kwargs)
    # Reviewed: same checks as run() above.
    # nosemgrep: dangerous-subprocess-use-audit
    return subprocess.Popen(_argv(args), **kwargs)  # nosec B603


def read_command_output(command: str) -> str:
    """Run an operator-configured command (for example a keychain lookup) and return its trimmed output.

    The command is split into words and started directly, without a shell, so pipes, redirects and `$(...)` are not
    interpreted. Put anything more complex in a small script and point the command at it.
    """
    return run(shlex.split(command), _operator_command=True, capture_output=True, text=True, check=True).stdout.strip()
