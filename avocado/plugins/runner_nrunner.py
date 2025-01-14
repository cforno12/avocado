# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: Red Hat Inc. 2019-2020
# Authors: Cleber Rosa <crosa@redhat.com>

"""
NRunner based implementation of job compliant runner
"""

import asyncio
import multiprocessing
import random
from copy import deepcopy

from avocado.core import nrunner
from avocado.core.dispatcher import SpawnerDispatcher
from avocado.core.exceptions import TestFailFast
from avocado.core.messages import MessageHandler
from avocado.core.output import LOG_JOB
from avocado.core.plugin_interfaces import CLI, Init
from avocado.core.plugin_interfaces import Runner as RunnerInterface
from avocado.core.requirements.resolver import RequirementsResolver
from avocado.core.settings import settings
from avocado.core.status.repo import StatusRepo
from avocado.core.status.server import StatusServer
from avocado.core.task.runtime import RuntimeTask
from avocado.core.task.statemachine import TaskStateMachine, Worker
from avocado.core.test_id import TestID
from avocado.core.varianter import dump_variant


class RunnerInit(Init):

    name = 'nrunner'
    description = 'nrunner initialization'

    def initialize(self):
        section = 'nrunner'
        help_msg = 'Shuffle the tasks to be executed'
        settings.register_option(section=section,
                                 key='shuffle',
                                 default=False,
                                 help_msg=help_msg,
                                 key_type=bool)

        help_msg = ('URI for listing the status server. Usually '
                    'a "HOST:PORT" string')
        settings.register_option(section=section,
                                 key='status_server_listen',
                                 default='127.0.0.1:8888',
                                 metavar="HOST:PORT",
                                 help_msg=help_msg)

        help_msg = ('URI for connecting to the status server, usually '
                    'a "HOST:PORT" string. Use this if your status server '
                    'is in another host, or different port')
        settings.register_option(section=section,
                                 key='status_server_uri',
                                 default='127.0.0.1:8888',
                                 metavar="HOST:PORT",
                                 help_msg=help_msg)

        help_msg = ('Buffer size that status server uses.  This should '
                    'generally not be a concern to most users, but '
                    'it can be tunned in case a runner generates very large '
                    'status messages, which is common if a test generates a '
                    'lot of output. Default is 33554432 (32MiB)')
        settings.register_option(section=section,
                                 key='status_server_buffer_size',
                                 key_type=int,
                                 default=2 ** 25,
                                 help_msg=help_msg)

        help_msg = ('Number of maximum number tasks running in parallel. You '
                    'can disable parallel execution by setting this to 1. '
                    'Defaults to the amount of CPUs on this machine.')
        settings.register_option(section=section,
                                 key='max_parallel_tasks',
                                 default=multiprocessing.cpu_count(),
                                 key_type=int,
                                 help_msg=help_msg)

        help_msg = ("Spawn tasks in a specific spawner. Available spawners: "
                    "'process' and 'podman'")
        settings.register_option(section=section,
                                 key="spawner",
                                 default='process',
                                 help_msg=help_msg)

        help_msg = "The amount of time a test has to complete in seconds."
        settings.register_option(section='task.timeout',
                                 key='running',
                                 default=None,
                                 key_type=int,
                                 help_msg=help_msg)


class RunnerCLI(CLI):

    name = 'nrunner'
    description = 'nrunner command line options for "run"'

    def configure(self, parser):
        super(RunnerCLI, self).configure(parser)
        parser = parser.subcommands.choices.get('run', None)
        if parser is None:
            return

        parser = parser.add_argument_group('nrunner specific options')
        settings.add_argparser_to_option(namespace='nrunner.shuffle',
                                         parser=parser,
                                         long_arg='--nrunner-shuffle',
                                         action='store_true')

        settings.add_argparser_to_option(
            namespace='nrunner.status_server_listen',
            parser=parser,
            long_arg='--nrunner-status-server-listen',
            metavar='HOST_PORT')

        settings.add_argparser_to_option(
            namespace='nrunner.status_server_uri',
            parser=parser,
            long_arg='--nrunner-status-server-uri',
            metavar='HOST_PORT')

        settings.add_argparser_to_option(
            namespace='nrunner.max_parallel_tasks',
            parser=parser,
            long_arg='--nrunner-max-parallel-tasks',
            metavar='NUMBER_OF_TASKS')

        settings.add_argparser_to_option(
            namespace='nrunner.spawner',
            parser=parser,
            long_arg='--nrunner-spawner',
            metavar='SPAWNER')

    def run(self, config):
        pass


class Runner(RunnerInterface):

    name = 'nrunner'
    description = 'nrunner based implementation of job compliant runner'

    @staticmethod
    def _get_requirements_runtime_tasks(runnable, prefix):
        if runnable.requirements is None:
            return

        # creates the runnables for the requirements
        requirements_runnables = RequirementsResolver.resolve(runnable)
        requirements_runtime_tasks = []
        # creates the tasks and runtime tasks for the requirements
        for requirement_runnable in requirements_runnables:
            name = '%s-%s' % (requirement_runnable.kind,
                              requirement_runnable.kwargs.get('name'))
            # the human UI works with TestID objects, so we need to
            # use it to name other tasks
            task_id = TestID(prefix,
                             name,
                             None)
            # with --dry-run we don't want to run requirement
            if runnable.kind == 'dry-run':
                requirement_runnable.kind = 'noop'
            # creates the requirement task
            requirement_task = nrunner.Task(requirement_runnable,
                                            identifier=task_id,
                                            category='requirement')
            # make sure we track the dependencies of a task
            # runtime_task.task.dependencies.add(requirement_task)
            # created the requirement runtime task
            requirements_runtime_tasks.append(RuntimeTask(requirement_task))

        return requirements_runtime_tasks

    @staticmethod
    def _create_runtime_tasks_for_test(test_suite, runnable, no_digits,
                                       index, variant):
        """Creates runtime tasks for both tests, and for its requirements."""
        result = []

        # test related operations
        # create test ID
        if test_suite.name:
            prefix = "{}-{}".format(test_suite.name, index)
        else:
            prefix = index
        test_id = TestID(prefix,
                         runnable.uri,
                         variant,
                         no_digits)
        # inject variant on runnable
        runnable.variant = dump_variant(variant)

        # handles the test task
        task = nrunner.Task(runnable,
                            identifier=test_id,
                            known_runners=nrunner.RUNNERS_REGISTRY_PYTHON_CLASS)
        runtime_task = RuntimeTask(task)
        result.append(runtime_task)

        # handles the requirements
        requirements_runtime_tasks = (
            Runner._get_requirements_runtime_tasks(runnable,
                                                   prefix))
        # extend the list of tasks with the requirements runtime tasks
        if requirements_runtime_tasks is not None:
            for requirement_runtime_task in requirements_runtime_tasks:
                # make sure we track the dependencies of a task
                runtime_task.task.dependencies.add(
                    requirement_runtime_task.task)
            result.extend(requirements_runtime_tasks)

        return result

    @staticmethod
    def _get_all_runtime_tasks(test_suite):
        runtime_tasks = []
        test_result_total = test_suite.variants.get_number_of_tests(test_suite.tests)
        no_digits = len(str(test_result_total))
        # define execution order
        execution_order = test_suite.config.get('run.execution_order')
        if execution_order == "variants-per-test":
            test_variant = [(test, variant) for test in test_suite.tests
                            for variant in test_suite.variants.itertests()]
        elif execution_order == "tests-per-variant":
            test_variant = [(test, variant)
                            for variant in test_suite.variants.itertests()
                            for test in test_suite.tests]

        # decide if a copy of the runnable is needed, in case of more
        # variants than tests
        copy_runnable = len(test_variant) > len(test_suite.tests)
        # create runtime tasks
        for index, (runnable, variant) in enumerate(test_variant, start=1):
            if copy_runnable:
                runnable = deepcopy(runnable)
            runtime_tasks.extend(Runner._create_runtime_tasks_for_test(
                test_suite,
                runnable,
                no_digits,
                index,
                variant))
        return runtime_tasks

    def _start_status_server(self, status_server_listen):
        # pylint: disable=W0201
        self.status_repo = StatusRepo()
        # pylint: disable=W0201
        self.status_server = StatusServer(status_server_listen,
                                          self.status_repo)
        asyncio.ensure_future(self.status_server.serve_forever())

    async def _update_status(self, job):
        tasks_by_id = {str(runtime_task.task.identifier): runtime_task.task
                       for runtime_task in self.runtime_tasks}
        message_handler = MessageHandler()
        while True:
            try:
                (task_id, _, _, index) = \
                    self.status_repo.status_journal_summary.pop(0)

            except IndexError:
                await asyncio.sleep(0.05)
                continue

            message = self.status_repo.get_task_data(task_id, index)
            task = tasks_by_id.get(task_id)
            message_handler.process_message(message, task, job)

    def run_suite(self, job, test_suite):
        summary = set()

        test_suite.tests, _ = nrunner.check_runnables_runner_requirements(
            test_suite.tests)
        job.result.tests_total = test_suite.variants.get_number_of_tests(test_suite.tests)

        listen = test_suite.config.get('nrunner.status_server_listen')
        self._start_status_server(listen)

        # pylint: disable=W0201
        self.runtime_tasks = self._get_all_runtime_tasks(test_suite)
        if test_suite.config.get('nrunner.shuffle'):
            random.shuffle(self.runtime_tasks)
        test_ids = [rt.task.identifier for rt in self.runtime_tasks
                    if rt.task.category == 'test']
        tsm = TaskStateMachine(self.runtime_tasks, self.status_repo)
        spawner_name = test_suite.config.get('nrunner.spawner')
        spawner = SpawnerDispatcher(test_suite.config)[spawner_name].obj
        max_running = min(test_suite.config.get('nrunner.max_parallel_tasks'),
                          len(self.runtime_tasks))
        timeout = test_suite.config.get('task.timeout.running')
        workers = [Worker(state_machine=tsm,
                          spawner=spawner,
                          max_running=max_running,
                          task_timeout=timeout).run()
                   for _ in range(max_running)]
        asyncio.ensure_future(self._update_status(job))
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(asyncio.wait_for(asyncio.gather(*workers),
                                                     job.timeout or None))
        except (KeyboardInterrupt, asyncio.TimeoutError, TestFailFast) as ex:
            LOG_JOB.info(str(ex))
            job.interrupted_reason = str(ex)
            summary.add("INTERRUPTED")

        # Wait until all messages may have been processed by the
        # status_updater. This should be replaced by a mechanism
        # that only waits if there are missing status messages to
        # be processed, and, only for a given amount of time.
        # Tests with non received status will always show as SKIP
        # because of result reconciliation.
        loop.run_until_complete(asyncio.sleep(0.05))

        job.result.end_tests()
        self.status_server.close()

        # Update the overall summary with found test statuses, which will
        # determine the Avocado command line exit status
        summary.update([status.upper() for status in
                        self.status_repo.get_result_set_for_tasks(test_ids)])
        return summary
