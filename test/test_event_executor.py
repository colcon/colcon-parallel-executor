# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

from colcon_parallel_executor.event.executor import ParallelStatus


def test_parallel_status():
    status = ParallelStatus(['job1', 'job2'])
    assert status.processing == ('job1', 'job2')
