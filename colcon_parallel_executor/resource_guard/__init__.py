# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

import asyncio
from contextlib import AsyncExitStack
import traceback

from colcon_core.logging import colcon_logger
from colcon_core.plugin_system import instantiate_extensions
from colcon_core.plugin_system import order_extensions_grouped_by_priority

logger = colcon_logger.getChild(__name__)


class JobIncompleteError(Exception):
    """
    Raised when a job does not finish successfully.

    If `result` is None, the job was cleanly skipped before execution.
    If `result` is not None, the job failed with this non-zero exit code.
    """

    def __init__(self, result=None):  # noqa: D107
        super().__init__()
        self.result = result


class ResourceGuardExtensionPoint:
    """
    The interface for parallel execution resource guard extensions.

    A resource guard provider provides asynchronous context managers that
    act as gatekeepers to throttle job execution.
    """

    """The version of the resource guard extension interface."""
    EXTENSION_POINT_VERSION = '1.0'

    """
    The priority of resource guard extensions.

    Extensions with a higher priority value are processed earlier.
    """
    PRIORITY = 100

    def __init__(self):  # noqa: D107
        super().__init__()

    def add_arguments(self, *, parser):
        """
        Add command line arguments specific to the resource guard.

        :param parser: The argument parser
        """
        pass

    async def initialize(self, args):
        """
        Initialize the resource guard provider before starting job execution.

        :param args: The parsed command line arguments
        """
        pass

    def get_guard(self, job):
        """
        Get the asynchronous context manager (guard) for the specified job.

        If this guard does not apply to the job, return None.

        :param job: The job object
        :returns: An object implementing __aenter__ and __aexit__, or None
        """
        return None


def get_resource_guard_extensions(*, group_name=None):
    """
    Get the available resource guard extensions.

    :rtype: OrderedDict
    """
    if group_name is None:
        group_name = 'colcon_parallel_executor.resource_guard'

    try:
        extensions = instantiate_extensions(group_name)
    except Exception as e:  # noqa: B902, F841
        exc = traceback.format_exc()
        logger.error(
            'Exception in resource guard discovery: {e}\n{exc}'
            .format_map(locals()))
        return {}

    for name, extension in extensions.items():
        extension.RESOURCE_GUARD_NAME = name
    return order_extensions_grouped_by_priority(extensions)


def add_resource_guard_arguments(parser, *, extensions=None):
    """Add command line arguments for the resource guard extensions."""
    if extensions is None:
        extensions = get_resource_guard_extensions()
    for priority in extensions.keys():
        for name, guard in extensions[priority].items():
            try:
                retval = guard.add_arguments(parser=parser)
                assert retval is None, 'add_arguments() should return None'
            except Exception as e:  # noqa: B902
                logger.error(
                    'Failed to add arguments for resource guard: %s', e)


async def initialize_resource_guard_extensions(args, *, extensions=None):
    """
    Initialize the resource guard extensions.

    :param args: The parsed command line arguments
    """
    if extensions is None:
        extensions = get_resource_guard_extensions()
    guards = []
    for priority in extensions.keys():
        for name, guard in extensions[priority].items():
            try:
                retval = await guard.initialize(args)
                assert retval is None, 'initialize() should return None'
                guards.append(guard)
            except Exception as e:  # noqa: B902
                logger.error(
                    'Failed to initialize resource guard: %s', e)
    return guards


async def run_guarded_job(job, guards):
    """
    Acquire resource guards and execute the job inside their context.

    :param job: The job coroutine function
    :param guards: List of resource guard extension providers
    """
    try:
        async with AsyncExitStack() as stack:
            try:
                for provider in guards:
                    guard = provider.get_guard(job)
                    if guard is not None:
                        await stack.enter_async_context(guard)
            except asyncio.CancelledError:
                raise JobIncompleteError(None)
            await asyncio.sleep(0)
            result = await job()
            if result:
                raise JobIncompleteError(result)
            return result
    except JobIncompleteError as e:
        return e.result
