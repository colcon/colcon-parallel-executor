colcon-parallel-executor
========================

An extension for `colcon-core <https://github.com/colcon/colcon-core>`_ to process packages in parallel.

Limiting the jobs of each package build
---------------------------------------

``--parallel-workers`` limits how many packages are processed at the same time.
Each package build may still start as many jobs as the machine has cores, so the total number of compiler processes can reach the number of workers multiplied by the number of cores.

``--parallel-jobs-per-worker NUMBER`` caps the jobs of each package build.
The worst case is then the number of workers multiplied by this number, which makes the memory needed by a build predictable.

The option sets ``CMAKE_BUILD_PARALLEL_LEVEL`` for the duration of the build, which CMake 3.12 and newer pass to the native build tool, covering make, Ninja and MSBuild.
It also adds ``-j<NUMBER>`` to ``MAKEFLAGS`` so that make based builds outside of CMake are limited as well, unless ``MAKEFLAGS`` already limits the number of jobs or the load average, in which case that value is left untouched.
Build tools which read neither variable are not limited.
Without the option the environment is left untouched.
