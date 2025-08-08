# Copyright 2025 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0


class ParallelStatus:
    """An event containing the identifiers of in-progress jobs."""

    __slots__ = ('processing', )

    def __init__(self, processing):
        """
        Construct a ParallelStatus.

        :param processing list: The in-progress job identifiers
        """
        self.processing = tuple(processing)
