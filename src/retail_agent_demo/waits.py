# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deliberate waits: retry back-off, polling for a service to become ready, and letting a browser page settle."""

from __future__ import annotations

import threading


def pause(seconds: float) -> None:
    """Block the calling thread for `seconds`. Used where the code must wait on something outside its control."""
    threading.Event().wait(seconds)
