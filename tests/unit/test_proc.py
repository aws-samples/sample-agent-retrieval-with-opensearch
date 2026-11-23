# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The single place the project starts programs: list arguments only, never a shell."""

import subprocess  # nosec B404 - imported for constants or in the single audited helper
import sys
from pathlib import Path

import pytest

from retail_agent_demo import proc


def test_run_takes_an_argument_list_and_paths():
    out = proc.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "hi"
    out = proc.run([Path(sys.executable), "-c", "print(1)"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1"


@pytest.mark.parametrize("bad", ["echo hi", b"echo hi", [], ()])
def test_run_rejects_a_string_or_an_empty_list(bad):
    with pytest.raises(ValueError):
        proc.run(bad)


def test_shell_true_is_refused():
    with pytest.raises(ValueError):
        proc.run([sys.executable, "-c", "pass"], shell=True)  # nosec B604 - proves the helper refuses shell=True
    with pytest.raises(ValueError):
        proc.popen([sys.executable, "-c", "pass"], shell=True)  # nosec B604 - proves the helper refuses shell=True


def test_popen_starts_a_process():
    p = proc.popen([sys.executable, "-c", "print('x')"], stdout=-1, text=True)
    out, _ = p.communicate()
    assert out.strip() == "x" and p.returncode == 0


def test_password_command_runs_without_a_shell():
    # a pipe, a redirect and a substitution are plain words, not shell syntax
    out = proc.read_command_output(f"{sys.executable} -c 'import sys; print(\" \".join(sys.argv[1:]))' a|b >c $(x)")
    assert out == "a|b >c $(x)"


def test_password_command_output_is_trimmed():
    assert proc.read_command_output(f"{sys.executable} -c 'print(\"  secret  \")'") == "secret"


def test_a_failing_password_command_raises():
    with pytest.raises(subprocess.CalledProcessError):
        proc.read_command_output(f"{sys.executable} -c 'raise SystemExit(3)'")


@pytest.mark.parametrize("program", ["rm", "/bin/sh", "curl", "bash", "aws"])
def test_a_program_that_is_not_one_of_the_projects_tools_is_refused(program):
    with pytest.raises(ValueError, match="not a program this project starts"):
        proc.run([program, "x"])
    with pytest.raises(ValueError, match="not a program this project starts"):
        proc.popen([program, "x"])


@pytest.mark.parametrize("program", ["dot", "/opt/homebrew/bin/dot", "dot.exe"])
def test_the_projects_own_tools_pass_the_program_check(program):
    assert proc._argv([program, "-V"])[0] == program


def test_the_operator_s_password_command_may_be_any_program():
    # read_command_output is the one path that is the operator's own choice (a keychain lookup, a script)
    assert proc._argv(["security", "find-generic-password"], operator_command=True)[0] == "security"
