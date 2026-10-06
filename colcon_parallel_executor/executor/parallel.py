# Copyright 2016-2018 Dirk Thomas
# Licensed under the Apache License, Version 2.0

import asyncio
from concurrent.futures import ALL_COMPLETED
from concurrent.futures import FIRST_COMPLETED
from contextlib import suppress
from inspect import iscoroutinefunction
import logging
import os
import re
import signal
import sys
import traceback

from colcon_core.executor import ExecutorExtensionPoint
from colcon_core.executor import OnError
from colcon_core.logging import colcon_logger
from colcon_core.plugin_system import satisfies_version
from colcon_core.subprocess import new_event_loop
from colcon_core.subprocess import SIGINT_RESULT
from colcon_parallel_executor.event.executor import ParallelStatus

logger = colcon_logger.getChild(__name__)


def counting_number(value):
    """Convert a number greater than or equal to zero."""
    value = int(value)
    if value < 0:
        raise ValueError()
    return value


# This is the same pattern colcon-cmake uses to decide whether MAKEFLAGS
# already limits the number of jobs, in which case it doesn't pass its own
# -j and -l. It can't be imported from there because the executor should not
# depend on a build system extension. A shared helper in colcon-core could
# replace both copies.
_MAKEFLAGS_JOB_FLAG = re.compile(
    r'(?:^|\s)'
    r'(-?(?:j|l)(?:\s*[0-9]+|\s|$))'
    r'|'
    r'(?:^|\s)'
    r'((?:--)?(?:jobs|load-average)(?:(?:=|\s+)[0-9]+|(?:\s|$)))')


def get_job_limit_environment(jobs, env):
    """
    Get the environment variables limiting the jobs of each package build.

    ``CMAKE_BUILD_PARALLEL_LEVEL`` is always set, CMake 3.12 and newer pass
    it to the native build tool on the command line (which also covers
    Ninja) where it takes precedence over ``MAKEFLAGS``.

    ``MAKEFLAGS`` is only set when the existing value doesn't limit the
    number of jobs or the load average, in which case ``-j<jobs>`` is added
    in front of the existing flags (and after a first word of bundled single
    letter flags).
    An existing limit is left untouched.

    :param int jobs: The maximum number of jobs per package build
    :param dict env: The environment variables to merge with
    :returns: The environment variables to set
    :rtype: dict
    """
    variables = {'CMAKE_BUILD_PARALLEL_LEVEL': str(jobs)}
    makeflags = env.get('MAKEFLAGS', '')
    if not _MAKEFLAGS_JOB_FLAG.search(makeflags):
        flags = makeflags.split()
        # GNU make exports single letter flags bundled in the first word
        # without a leading dash. Everything after '--' is a variable
        # definition rather than a flag.
        index = 1 if flags and not flags[0].startswith('-') else 0
        flags.insert(index, '-j{jobs}'.format_map(locals()))
        variables['MAKEFLAGS'] = ' '.join(flags)
    return variables


def _update_environment(variables):
    """
    Update environment variables and return their previous values.

    :param dict variables: The environment variables to set, a value of
      ``None`` removes the variable
    :returns: The previous values in the same format
    :rtype: dict
    """
    previous = {}
    for name, value in variables.items():
        previous[name] = os.environ.get(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    return previous


class ParallelExecutorExtension(ExecutorExtensionPoint):
    """
    Process multiple packages in parallel.

    The parallelization is honoring the dependency graph between the packages.
    """

    # the priority needs to be higher than the extension providing the
    # sequential execution in order to become the default
    PRIORITY = 110

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            ExecutorExtensionPoint.EXTENSION_POINT_VERSION, '^1.1')

    def add_arguments(self, *, parser):  # noqa: D102
        max_workers_default = os.cpu_count() or 4
        with suppress(AttributeError):
            # consider restricted set of CPUs if applicable
            max_workers_default = min(
                max_workers_default, len(os.sched_getaffinity(0)))
        parser.add_argument(
            '--parallel-workers',
            type=counting_number,
            default=max_workers_default,
            metavar='NUMBER',
            help='The maximum number of packages to process in parallel, '
                 "or '0' for no limit "
                 '(default: {max_workers_default})'.format_map(locals()))
        parser.add_argument(
            '--parallel-jobs-per-worker',
            type=counting_number,
            default=0,
            metavar='NUMBER',
            help='The maximum number of jobs each package build may run in '
                 'parallel, e.g. compiler processes under make or Ninja, by '
                 'setting CMAKE_BUILD_PARALLEL_LEVEL and, unless it already '
                 "limits the jobs, MAKEFLAGS, or '0' for no limit "
                 '(default: 0)')

    def execute(self, args, jobs, *, on_error=OnError.interrupt):  # noqa: D102
        # avoid debug message from asyncio when colcon uses debug log level
        asyncio_logger = logging.getLogger('asyncio')
        asyncio_logger.setLevel(logging.INFO)

        loop = new_event_loop()
        asyncio.set_event_loop(loop)

        coro = self._execute(args, jobs, on_error=on_error)
        future = asyncio.ensure_future(coro, loop=loop)

        # Limit the number of jobs of each package build through the
        # environment inherited by the tasks
        previous_environment = {}
        jobs_per_worker = getattr(args, 'parallel_jobs_per_worker', 0)
        if jobs_per_worker:
            previous_environment = _update_environment(
                get_job_limit_environment(jobs_per_worker, os.environ))
            logger.debug(
                'limiting each package build to {jobs_per_worker} jobs'
                .format_map(locals()))

        try:
            logger.debug('run_until_complete')
            loop.run_until_complete(future)
        except KeyboardInterrupt:
            logger.debug('run_until_complete was interrupted')
            # override job rc with special SIGINT value
            for job in self._ongoing_jobs:
                job.returncode = SIGINT_RESULT
            # ignore further SIGINTs
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            # wait for jobs which have also received a SIGINT
            if not future.done():
                logger.debug('run_until_complete again')
                loop.run_until_complete(future)
                assert future.done()
            # read potential exception to avoid asyncio error
            _ = future.exception()
            logger.debug('run_until_complete finished')
            return signal.SIGINT
        except Exception as e:  # noqa: F841
            exc = traceback.format_exc()
            logger.error(
                'Exception in job execution: {e}\n{exc}'.format_map(locals()))
            return 1
        finally:
            _update_environment(previous_environment)
            # HACK on Windows closing the event loop seems to hang after Ctrl-C
            # even though no futures are pending, but appears fixed in py3.8
            if sys.platform != 'win32' or sys.version_info >= (3, 8):
                logger.debug('closing loop')
                loop.close()
                logger.debug('loop closed')
            else:
                logger.debug('skipping loop closure')
        result = future.result()
        logger.debug(
            "run_until_complete finished with '{result}'".format_map(locals()))
        return result

    async def _execute(self, args, jobs, *, on_error):
        # count the number of dependent jobs for each job
        # in order to process jobs with more dependent jobs first
        recursive_dependent_counts = {}
        for package_name, job in jobs.items():
            # ignore "self" dependency
            recursive_dependent_counts[package_name] = len([
                j for name, j in jobs.items()
                if package_name != name and package_name in j.dependencies])

        futures = {}
        finished_jobs = {}
        rc = 0
        jobs = jobs.copy()
        while jobs or futures:
            # determine "ready" jobs
            ready_jobs = []
            for package_name, job in jobs.items():
                # a pending job is "ready" when all dependencies have finished
                not_finished = set(jobs.keys()) | {
                    f.identifier for f in futures.values()}
                if not (set(job.dependencies) - {package_name}) & not_finished:
                    ready_jobs.append((
                        package_name, job,
                        recursive_dependent_counts[package_name]))

            # order the ready jobs, jobs with more dependents first
            ready_jobs.sort(key=lambda r: -r[2])

            # take "ready" jobs
            take_jobs = []
            for package_name, job, _ in ready_jobs:
                # don't schedule more jobs then workers
                # to prevent starting further jobs when a job fails
                if args.parallel_workers:
                    if len(futures) + len(take_jobs) >= args.parallel_workers:
                        break
                take_jobs.append((package_name, job))
                del jobs[package_name]

            # pass them to the executor
            for package_name, job in take_jobs:
                assert iscoroutinefunction(job.__call__), \
                    'Job is not a coroutine'
                future = asyncio.ensure_future(job())
                futures[future] = job

            # wait for futures
            assert futures, 'No futures'
            self._ongoing_jobs = futures.values()
            done_futures, _pending = await asyncio.wait(
                futures.keys(), timeout=30, return_when=FIRST_COMPLETED)

            if not done_futures:  # timeout
                self.put_event_into_queue(ParallelStatus(tuple(
                    f.identifier for f in futures.values())))

            # check results of done futures
            for done_future in [
                f for f in futures.keys() if f in done_futures
            ]:
                job = futures[done_future]
                del futures[done_future]
                # get result without raising an exception
                if done_future.cancelled():
                    result = signal.SIGINT
                elif done_future.exception():
                    result = done_future.exception()
                    if isinstance(result, KeyboardInterrupt):
                        result = signal.SIGINT
                else:
                    result = done_future.result()
                    if result == SIGINT_RESULT:
                        result = signal.SIGINT
                finished_jobs[job.identifier] = result
                # if any job returned a SIGINT overwrite the return code
                # this should override a potentially earlier set error code
                # in the case where on_error isn't set to OnError.interrupt
                # otherwise set the error code if it is the first
                if result is signal.SIGINT or result and not rc:
                    rc = result

                if result:
                    if on_error in (OnError.interrupt, OnError.skip_pending):
                        # skip pending jobs
                        jobs.clear()

                    if on_error == OnError.skip_downstream:
                        # skip downstream jobs of failed one
                        for pending_name, pending_job in list(jobs.items()):
                            if job.identifier in pending_job.dependencies:
                                del jobs[pending_name]

            # if any job failed or was interrupted cancel pending futures
            if (rc and on_error == OnError.interrupt) or rc is signal.SIGINT:
                if futures:
                    for future in futures.keys():
                        if not future.done():
                            future.cancel()
                    await asyncio.wait(
                        futures.keys(), return_when=ALL_COMPLETED)
                    # collect results from canceled futures
                    for future, job in futures.items():
                        result = future.result()
                        finished_jobs[job.identifier] = result
                break

        # if any job failed
        if any(finished_jobs.values()):
            # flush job output
            self._flush()

        return rc
