# Copyright 2025 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

import sys

from colcon_core.event_handler import EventHandlerExtensionPoint
from colcon_core.output_style import Style
from colcon_core.plugin_system import satisfies_version
from colcon_parallel_executor.event.executor import ParallelStatus


class ParallelStatusEventHandler(EventHandlerExtensionPoint):
    """
    Periodically print a reminder of the currently executing jobs.

    This extension is only enabled by default if stdout is a tty-like device.

    This extension handles events of the following types:
    - :py:class:`colcon_parallel_executor.event.executor.ParallelStatus`
    """

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            EventHandlerExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

        self.enabled = sys.stdout.isatty()

    def __call__(self, event):  # noqa: D102
        data = event[0]

        if isinstance(data, ParallelStatus):
            jobs = [
                Style.PackageOrJobName(job)
                for job in sorted(data.processing)
            ]
            print(f"[Processing: {', '.join(jobs)}]")
