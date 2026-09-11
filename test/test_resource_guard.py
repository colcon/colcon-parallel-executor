# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

import argparse
import asyncio
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import patch

from colcon_core.executor import Job
from colcon_parallel_executor.executor.parallel \
    import ParallelExecutorExtension
from colcon_parallel_executor.resource_guard import \
    add_resource_guard_arguments
from colcon_parallel_executor.resource_guard import \
    initialize_resource_guard_extensions
from colcon_parallel_executor.resource_guard import \
    ResourceGuardExtensionPoint
from colcon_parallel_executor.resource_guard.worker_limiter import \
    WorkerLimiterGuard
from run_until_complete import run_until_complete

ran_jobs = []


class Job1(Job):

    def __init__(self, identifier='job1', dependencies=None):
        super().__init__(
            identifier=identifier,
            dependencies=dependencies or set(),
            task=None,
            task_context=None
        )

    async def __call__(self, *args, **kwargs):
        ran_jobs.append(self.identifier)


def test_worker_limiter_guard():
    """Verify WorkerLimiterGuard correctly limits concurrency."""
    guard_provider = WorkerLimiterGuard()
    args = SimpleNamespace(parallel_workers=2)
    run_until_complete(guard_provider.initialize(args))

    # Returns an asyncio.Semaphore instance
    sem = guard_provider.get_guard(Job1('job1'))
    assert isinstance(sem, asyncio.Semaphore)
    assert sem._value == 2

    # If parallel_workers is 0, no semaphore (None) is returned
    args_zero = SimpleNamespace(parallel_workers=0)
    run_until_complete(guard_provider.initialize(args_zero))
    sem = guard_provider.get_guard(Job1('job1'))
    assert sem is None


def test_worker_limiter_guard_edge_cases():
    """Verify WorkerLimiterGuard argument parsing and empty edge cases."""
    guard_provider = WorkerLimiterGuard()
    # add_arguments should return None
    parser = argparse.ArgumentParser()
    assert guard_provider.add_arguments(parser=parser) is None

    # Missing parallel_workers attribute should fall back to 0 (no limit)
    run_until_complete(guard_provider.initialize(SimpleNamespace()))
    sem = guard_provider.get_guard(Job1('job1'))
    assert sem is None


def test_parallel_no_extensions():
    """Verify execution is unrestricted when no extensions are enabled."""
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['one'] = Job1('job1')
    jobs['two'] = Job1('job2')

    with patch(
        'colcon_parallel_executor.resource_guard.'
        'instantiate_extensions',
        return_value={}
    ):
        rc = extension.execute(args, jobs)
    assert rc == 0
    assert set(ran_jobs) == {'job1', 'job2'}
    ran_jobs.clear()


def test_custom_resource_guard():
    """Verify a custom capacity limiter can withhold jobs via async context."""
    class CustomThrottler(ResourceGuardExtensionPoint):
        """Custom limiter that blocks the second job until first completes."""

        PRIORITY = 1500

        async def initialize(self, args):
            self.allow_second = False
            self.first_job_future = asyncio.Future()

        def get_guard(self, job):
            return CustomGuardContext(self, job.identifier)

    class CustomGuardContext:

        def __init__(self, provider, package_name):
            self.provider = provider
            self.package_name = package_name

        async def __aenter__(self):
            if self.package_name == 'two':
                # Block second job until the first job finishes and
                # resolves the future
                await self.provider.first_job_future
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if self.package_name == 'one':
                # First job finished, wake up the second job
                self.provider.allow_second = True
                self.provider.first_job_future.set_result(True)

    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['one'] = Job1('job1')
    jobs['two'] = Job1('job2')

    throttler = CustomThrottler()

    with patch(
        'colcon_parallel_executor.resource_guard.'
        'instantiate_extensions',
        return_value={'custom_throttler': throttler}
    ):
        rc = extension.execute(args, jobs)

    assert rc == 0
    assert set(ran_jobs) == {'job1', 'job2'}
    ran_jobs.clear()


def test_initialize_resource_guard_extensions():
    """Verify initialize_resource_guard_extensions initializes correctly."""
    class MockGuard(ResourceGuardExtensionPoint):

        def __init__(self):
            super().__init__()
            self.called = False

        async def initialize(self, args):
            self.called = True

    mock_guard = MockGuard()
    extensions = OrderedDict([
        (100, {'mock_guard': mock_guard})
    ])

    args = SimpleNamespace()
    guards = run_until_complete(
        initialize_resource_guard_extensions(args, extensions=extensions))
    assert guards == [mock_guard]
    assert mock_guard.called


def test_initialize_resource_guard_extensions_exception():
    """Verify initialize_resource_guard_extensions handles exceptions."""
    class MockGuard(ResourceGuardExtensionPoint):

        async def initialize(self, args):
            raise RuntimeError('init error')

    mock_guard = MockGuard()
    extensions = OrderedDict([
        (100, {'mock_guard': mock_guard})
    ])

    args = SimpleNamespace()
    guards = run_until_complete(
        initialize_resource_guard_extensions(args, extensions=extensions))
    assert guards == []


def test_add_resource_guard_arguments_exception():
    """Verify add_resource_guard_arguments handles exceptions gracefully."""
    class MockGuard(ResourceGuardExtensionPoint):

        def add_arguments(self, *, parser):
            raise RuntimeError('parser error')

    mock_guard = MockGuard()
    extensions = OrderedDict([
        (100, {'mock_guard': mock_guard})
    ])

    parser = object()
    # This should handle the exception internally and not raise
    add_resource_guard_arguments(parser, extensions=extensions)
