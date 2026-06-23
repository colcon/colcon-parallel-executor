# Copyright 2016-2018 Dirk Thomas
# Licensed under the Apache License, Version 2.0

import argparse
import asyncio
from collections import OrderedDict
import os
import signal
import sys
from threading import Thread
import time
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from colcon_core.executor import Job
from colcon_core.executor import OnError
from colcon_core.subprocess import SIGINT_RESULT
from colcon_parallel_executor.executor.parallel import counting_number
from colcon_parallel_executor.executor.parallel \
    import ParallelExecutorExtension
import pytest

ran_jobs = []


class Job1(Job):

    def __init__(self, identifier='job1'):
        super().__init__(
            identifier=identifier, dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        ran_jobs.append(self.identifier)


class Job2(Job):

    def __init__(self):
        super().__init__(
            identifier='job2', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        return 2


class Job3(Job):

    def __init__(self):
        super().__init__(
            identifier='job3', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        raise RuntimeError('custom exception')


class Job4(Job):

    def __init__(self):
        super().__init__(
            identifier='job4', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return SIGINT_RESULT
        ran_jobs.append(self.identifier)


class Job5(Job):

    def __init__(self):
        super().__init__(
            identifier='job5', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        return 5


class Job6(Job):

    def __init__(self):
        super().__init__(
            identifier='job6', dependencies=('job2', ), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        ran_jobs.append(self.identifier)


class Job7(Job):

    def __init__(self):
        super().__init__(
            identifier='job7', dependencies=('job1', ), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        ran_jobs.append(self.identifier)


def test_parallel():
    extension = ParallelExecutorExtension()

    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['one'] = Job1()

    # success
    rc = extension.execute(args, jobs)
    assert rc == 0
    assert ran_jobs == ['job1']
    ran_jobs.clear()

    # return error code
    jobs['two'] = Job2()
    jobs['four'] = Job4()
    rc = extension.execute(args, jobs)
    assert rc == 2
    assert ran_jobs == ['job1']
    ran_jobs.clear()

    rc = extension.execute(args, jobs, on_error=OnError.skip_pending)
    assert rc == 2
    assert ran_jobs == ['job1']
    ran_jobs.clear()

    # return error code, but with more workers
    args.parallel_workers = 3
    rc = extension.execute(args, jobs)
    assert rc == 2
    assert ran_jobs == ['job1']
    ran_jobs.clear()

    rc = extension.execute(args, jobs, on_error=OnError.skip_pending)
    assert rc == 2
    assert ran_jobs == ['job1', 'job4']
    ran_jobs.clear()

    # continue after error, keeping first error code
    jobs['five'] = Job5()
    rc = extension.execute(args, jobs, on_error=OnError.continue_)
    assert rc == 2
    assert ran_jobs == ['job1', 'job4']
    ran_jobs.clear()

    # continue but skip downstream
    jobs['six'] = Job6()
    jobs['seven'] = Job7()
    rc = extension.execute(args, jobs, on_error=OnError.skip_downstream)
    assert rc == 2
    assert ran_jobs == ['job1', 'job7', 'job4']
    ran_jobs.clear()

    # exception
    jobs['two'] = Job3()
    rc = extension.execute(args, jobs)
    assert isinstance(rc, RuntimeError)
    assert ran_jobs == ['job1']
    ran_jobs.clear()


class Job8(Job):

    def __init__(self):
        super().__init__(
            identifier='job8', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        await asyncio.sleep(3)
        ran_jobs.append(self.identifier)


@pytest.fixture
def restore_sigint_handler():
    handler = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, handler)


def test_parallel_keyboard_interrupt(restore_sigint_handler):
    if sys.platform == 'win32':
        pytest.skip(
            'Skipping keyboard interrupt test since the signal will cause '
            'pytest to return failure even if no tests fail.')

    extension = ParallelExecutorExtension()

    args = SimpleNamespace(parallel_workers=3)
    jobs = OrderedDict()
    jobs['one'] = Job1()
    jobs['aborted'] = Job8()
    jobs['four'] = Job4()

    def delayed_sigint():
        time.sleep(0.1)
        # Note: a real Ctrl-C would signal the whole process group
        os.kill(
            os.getpid(),
            signal.SIGINT if sys.platform != 'win32' else signal.CTRL_C_EVENT)
        if sys.platform == 'win32':
            os.kill(os.getpid(), signal.CTRL_C_EVENT)

    thread = Thread(target=delayed_sigint)
    thread.start()
    try:
        rc = extension.execute(args, jobs)
    finally:
        thread.join()

    assert rc == signal.SIGINT
    ran_jobs.clear()


def test_counting_number():
    assert counting_number('0') == 0
    assert counting_number(1) == 1
    with pytest.raises(ValueError):
        counting_number('-1')


def test_add_arguments():
    parser = argparse.ArgumentParser()
    extension = ParallelExecutorExtension()
    extension.add_arguments(parser=parser)
    args = parser.parse_args(['--parallel-workers', '5'])
    assert args.parallel_workers == 5


def test_parallel_run_until_complete_exception():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['one'] = Job1()

    def mock_run(future):
        future.get_coro().close()
        raise RuntimeError('mock error')

    with patch(
        'asyncio.base_events.BaseEventLoop.run_until_complete',
        side_effect=mock_run
    ):
        rc = extension.execute(args, jobs)
    assert rc == 1


def test_parallel_timeout():
    extension = ParallelExecutorExtension()
    extension.put_event_into_queue = Mock()

    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['eight'] = Job8()  # sleep 3

    original_wait = asyncio.wait

    def mock_wait(*args, **kwargs):
        kwargs['timeout'] = 0.01
        return original_wait(*args, **kwargs)

    with patch(
        'colcon_parallel_executor.executor.parallel.asyncio.wait',
        new=mock_wait
    ):
        rc = extension.execute(args, jobs)

    assert rc == 0
    assert extension.put_event_into_queue.called
    ran_jobs.clear()


class Job9(Job):

    def __init__(self):
        super().__init__(
            identifier='job9', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        raise KeyboardInterrupt()


def test_parallel_job_keyboard_interrupt():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['nine'] = Job9()
    rc = extension.execute(args, jobs)
    assert rc == signal.SIGINT


class Job10(Job):

    def __init__(self):
        super().__init__(
            identifier='job10', dependencies=set(), task=None,
            task_context=None)

    async def __call__(self, *args, **kwargs):
        return SIGINT_RESULT


def test_parallel_job_sigint_result():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['ten'] = Job10()
    rc = extension.execute(args, jobs)
    assert rc == signal.SIGINT


def test_parallel_future_keyboard_interrupt():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['eleven'] = Job1()

    original_wait = asyncio.wait

    async def mock_wait(*args, **kwargs):
        done, pending = await original_wait(*args, **kwargs)
        for f in done:
            f.exception = Mock(return_value=KeyboardInterrupt())
        return done, pending

    with patch(
        'colcon_parallel_executor.executor.parallel.asyncio.wait',
        new=mock_wait
    ):
        rc = extension.execute(args, jobs)

    assert rc == signal.SIGINT


def test_parallel_future_cancelled():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['twelve'] = Job1()

    original_wait = asyncio.wait

    async def mock_wait(*args, **kwargs):
        done, pending = await original_wait(*args, **kwargs)
        for f in done:
            f.cancelled = Mock(return_value=True)
        return done, pending

    with patch(
        'colcon_parallel_executor.executor.parallel.asyncio.wait',
        new=mock_wait
    ):
        rc = extension.execute(args, jobs)

    assert rc == signal.SIGINT


def test_priority():
    extension = ParallelExecutorExtension()
    assert extension.PRIORITY > 100


class NonCoroutineJob(Job):

    def __init__(self):
        super().__init__(
            identifier='non_coro', dependencies=set(), task=None,
            task_context=None)

    def __call__(self, *args, **kwargs):
        pass


def test_job_not_coroutine():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=2)
    jobs = OrderedDict()
    jobs['one'] = NonCoroutineJob()

    rc = extension.execute(args, jobs)
    assert rc == 1


def test_parallel_workers_zero():
    extension = ParallelExecutorExtension()
    args = SimpleNamespace(parallel_workers=0)
    jobs = OrderedDict()
    jobs['one'] = Job1('job1')
    jobs['two'] = Job1('job2')

    rc = extension.execute(args, jobs)
    assert rc == 0
    assert set(ran_jobs) == {'job1', 'job2'}
    ran_jobs.clear()
