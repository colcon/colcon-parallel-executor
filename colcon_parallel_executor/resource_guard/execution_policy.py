# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

from colcon_core.executor import OnError
from colcon_parallel_executor.resource_guard import JobIncompleteError
from colcon_parallel_executor.resource_guard import \
    ResourceGuardExtensionPoint


class ExecutionPolicyGuard(ResourceGuardExtensionPoint):
    """
    An implicit resource guard that cleanly skips pending jobs.

    This is activated when a failure has occurred during execution.
    """

    def __init__(self, on_error):  # noqa: D107
        super().__init__()
        self.on_error = on_error
        self.skip_all = False

    def get_guard(self, job):  # noqa: D102
        return self

    async def __aenter__(self):  # noqa: D105
        if self.skip_all:
            raise JobIncompleteError()

    async def __aexit__(self, exc_type, exc_val, exc_tb):  # noqa: D105
        if (
            exc_type is not None and
            self.on_error in (OnError.interrupt, OnError.skip_pending) and
            (exc_type is not JobIncompleteError or exc_val.result is not None)
        ):
            self.skip_all = True
        return False
