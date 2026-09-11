# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

import asyncio

from colcon_core.plugin_system import satisfies_version
from colcon_parallel_executor.resource_guard import ResourceGuardExtensionPoint


class WorkerLimiterGuard(ResourceGuardExtensionPoint):
    """Limits concurrent jobs using a semaphore."""

    PRIORITY = 100

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            ResourceGuardExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    async def initialize(self, args):  # noqa: D102
        workers = getattr(args, 'parallel_workers', 0)
        self._semaphore = asyncio.Semaphore(workers) if workers > 0 else None

    def get_guard(self, job):  # noqa: D102
        return self._semaphore
