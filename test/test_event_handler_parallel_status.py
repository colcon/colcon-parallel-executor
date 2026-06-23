# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

from unittest.mock import patch

from colcon_parallel_executor.event.executor import ParallelStatus
from colcon_parallel_executor.event_handler.parallel_status \
    import ParallelStatusEventHandler
import pytest


@pytest.mark.parametrize(('isatty'), (
    (True,),
    (False,),
))
def test_parallel_status_event_handler(capsys, isatty):
    with patch('sys.stdout.isatty', return_value=isatty):
        handler = ParallelStatusEventHandler()
        assert handler.enabled == isatty

    event = (ParallelStatus(['job2', 'job1']), )
    handler(event)

    if not isatty:
        return

    captured = capsys.readouterr()
    assert 'Processing' in captured.out
    assert 'job1' in captured.out
    assert 'job2' in captured.out

    # Test with non-matching event
    handler(('string event',))
    captured = capsys.readouterr()
    assert captured.out == ''
