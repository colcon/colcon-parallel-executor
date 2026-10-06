# Copyright 2026 Civ Robotics
# Licensed under the Apache License, Version 2.0

import argparse
from collections import OrderedDict
import os
from types import SimpleNamespace
from unittest.mock import patch

from colcon_core.executor import Job
from colcon_core.executor import OnError
from colcon_parallel_executor.executor.parallel \
    import get_job_limit_environment
from colcon_parallel_executor.executor.parallel \
    import ParallelExecutorExtension
import pytest

JOB_LIMIT_VARIABLES = ('MAKEFLAGS', 'CMAKE_BUILD_PARALLEL_LEVEL')


class RecordingJob(Job):
    """A job recording the environment variables it is running with."""

    def __init__(self, identifier='recording', rc=0):
        super().__init__(
            identifier=identifier, dependencies=set(), task=None,
            task_context=None)
        self.rc = rc
        self.environment = None

    async def __call__(self, *args, **kwargs):
        self.environment = {
            name: os.environ.get(name) for name in JOB_LIMIT_VARIABLES}
        return self.rc


class InterruptingJob(Job):

    def __init__(self):
        super().__init__(
            identifier='interrupting', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        raise KeyboardInterrupt()


@pytest.fixture
def clean_environment():
    with patch.dict(os.environ):
        for name in JOB_LIMIT_VARIABLES:
            os.environ.pop(name, None)
        yield


def test_add_arguments():
    parser = argparse.ArgumentParser()
    extension = ParallelExecutorExtension()
    extension.add_arguments(parser=parser)

    args = parser.parse_args([])
    assert args.parallel_jobs_per_worker == 0

    args = parser.parse_args(['--parallel-jobs-per-worker', '4'])
    assert args.parallel_jobs_per_worker == 4

    # Zero means no limit, like --parallel-workers
    args = parser.parse_args(['--parallel-jobs-per-worker', '0'])
    assert args.parallel_jobs_per_worker == 0

    for invalid in ('-2', 'many'):
        with pytest.raises(SystemExit):
            parser.parse_args(['--parallel-jobs-per-worker', invalid])


@pytest.mark.parametrize('makeflags,expected', [
    # No existing limit: -j is added in front of the flags
    (None, '-j4'),
    ('', '-j4'),
    ('-k', '-j4 -k'),
    ('--no-print-directory -k', '-j4 --no-print-directory -k'),
    # Variable definitions after '--' stay after it
    (' -- N=2 J=2', '-j4 -- N=2 J=2'),
    # Bundled single letter flags stay the first word
    ('s', 's -j4'),
    ('w -- FOO=1', 'w -j4 -- FOO=1'),
    ('kw --warn-undefined-variables', 'kw -j4 --warn-undefined-variables'),
    # An existing limit is left untouched
    ('-j', None),
    ('-j8', None),
    ('-j 8', None),
    ('j8', None),
    ('--jobs', None),
    ('--jobs=8', None),
    ('--jobs 8', None),
    ('-l8', None),
    ('-l 8', None),
    ('--load-average=8', None),
    ('--load-average 8', None),
    ('-s -j8 --no-print-directory', None),
    ('w -j8 --jobserver-auth=3,4', None),
    ('-j8 -- N=2', None),
])
def test_get_job_limit_environment(makeflags, expected):
    env = {} if makeflags is None else {'MAKEFLAGS': makeflags}
    variables = get_job_limit_environment(4, env)
    assert variables['CMAKE_BUILD_PARALLEL_LEVEL'] == '4'
    assert variables.get('MAKEFLAGS') == expected


def test_get_job_limit_environment_overrides_cmake_level():
    variables = get_job_limit_environment(3, {
        'MAKEFLAGS': '-j16',
        'CMAKE_BUILD_PARALLEL_LEVEL': '16',
    })
    assert variables == {'CMAKE_BUILD_PARALLEL_LEVEL': '3'}


def test_execute_without_limit_leaves_environment(clean_environment):
    os.environ['MAKEFLAGS'] = '-j16 -s'
    before = dict(os.environ)

    extension = ParallelExecutorExtension()
    job = RecordingJob()
    jobs = OrderedDict(recording=job)

    # The argument is absent entirely, as for older callers
    rc = extension.execute(SimpleNamespace(parallel_workers=2), jobs)
    assert rc == 0
    assert job.environment['MAKEFLAGS'] == '-j16 -s'
    assert job.environment['CMAKE_BUILD_PARALLEL_LEVEL'] is None
    assert dict(os.environ) == before

    # The argument is present with its default
    args = SimpleNamespace(parallel_workers=2, parallel_jobs_per_worker=0)
    rc = extension.execute(args, jobs)
    assert rc == 0
    assert job.environment['MAKEFLAGS'] == '-j16 -s'
    assert dict(os.environ) == before


def test_execute_with_limit_sets_and_restores(clean_environment):
    os.environ['MAKEFLAGS'] = '-s -- FOO=1'
    os.environ['CMAKE_BUILD_PARALLEL_LEVEL'] = '16'
    before = dict(os.environ)

    extension = ParallelExecutorExtension()
    job = RecordingJob()
    jobs = OrderedDict(recording=job)
    args = SimpleNamespace(parallel_workers=2, parallel_jobs_per_worker=2)

    rc = extension.execute(args, jobs)
    assert rc == 0
    assert job.environment == {
        'MAKEFLAGS': '-j2 -s -- FOO=1',
        'CMAKE_BUILD_PARALLEL_LEVEL': '2',
    }
    assert dict(os.environ) == before


def test_execute_with_limit_keeps_existing_makeflags(clean_environment):
    os.environ['MAKEFLAGS'] = '-j16 -s'
    before = dict(os.environ)

    extension = ParallelExecutorExtension()
    job = RecordingJob()
    jobs = OrderedDict(recording=job)
    args = SimpleNamespace(parallel_workers=2, parallel_jobs_per_worker=2)

    rc = extension.execute(args, jobs)
    assert rc == 0
    assert job.environment == {
        'MAKEFLAGS': '-j16 -s',
        'CMAKE_BUILD_PARALLEL_LEVEL': '2',
    }
    assert dict(os.environ) == before


def test_execute_restores_after_failure(clean_environment):
    before = dict(os.environ)

    extension = ParallelExecutorExtension()
    job = RecordingJob(rc=2)
    jobs = OrderedDict(recording=job)
    args = SimpleNamespace(parallel_workers=2, parallel_jobs_per_worker=1)

    rc = extension.execute(args, jobs, on_error=OnError.interrupt)
    assert rc == 2
    assert job.environment['MAKEFLAGS'] == '-j1'
    assert dict(os.environ) == before


def test_execute_restores_after_interrupt(clean_environment):
    before = dict(os.environ)

    extension = ParallelExecutorExtension()
    jobs = OrderedDict(interrupting=InterruptingJob())
    args = SimpleNamespace(parallel_workers=2, parallel_jobs_per_worker=1)

    extension.execute(args, jobs)
    assert dict(os.environ) == before
