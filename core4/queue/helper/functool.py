#
# Copyright 2018 Plan.Net Business Intelligence GmbH & Co. KG
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
The :mod:`functool <core4.queue.helper.functool` module implements the helper
functions :func:`enqueue` and :func:`execute`.
"""

import core4.logger
import core4.queue.main
import core4.queue.worker
import core4.service.introspect.main
import core4.service.setup
import core4.util.node
from core4.service.introspect.command import ENQUEUE_ARG


def enqueue(job, **kwargs):
    """
    Eenqueue a job. Uses the Python virtual environment of the project if
    ``config.folder.home`` is defined. If no core4 home folder is defined, the
    current environment is scanned for the job to be launched.

    :param job: qual_name or job class
    :param kwargs: arguments to be passed to the job

    :return: enqueued job object
    """
    if isinstance(job, str):
        name = job
    else:
        name = job.qual_name()
    queue = core4.queue.main.CoreQueue()
    home = queue.config.folder.home
    if home:
        intro = core4.service.introspect.main.CoreIntrospector()
        data = intro.retrospect()
        found = [d["name"] for d in data
                 if name in [i["name"] for i in d["jobs"]]]
        if found:
            stdout = core4.service.introspect.main.exec_project(
                found[0], ENQUEUE_ARG, qual_name=name,
                args="**%s" % (str(kwargs)), comm=True)
            return stdout
    return queue.enqueue(name=name, **kwargs)._id


class DirectWorker(core4.queue.worker.CoreWorker):

    def handle_signal(self):
        pass


def execute(job, **kwargs):
    """
    Enqueue and immediately execute a job in foreground. This
    method is used in development::

        execute(DummyJob, sleep=15)

    :param job: qual_name or job class
    :param kwargs: job arguments

    :return: final MongoDB job document from ``sys.queue``
    """
    if isinstance(job, str):
        name = job
    else:
        name = job.qual_name()
    setup = core4.service.setup.CoreSetup()
    setup.make_all()
    core4.logger.logon()
    queue = core4.queue.main.CoreQueue()
    job = queue.enqueue(name=name, **kwargs)
    job.manual = True
    worker = DirectWorker(name="manual")
    worker.at = core4.util.node.mongo_now()
    worker.start_job(job.serialise(), run_async=False)
    doc = worker.queue.job_detail(job._id)
    worker.config.sys.queue.delete_one(filter={"_id": job._id})
    worker.config.sys.journal.delete_one(filter={"_id": job._id})
    return doc


def find_job(**kwargs):
    """
    Finds the job using the passed ``kwargs`` as filter arguments.

    :param kwargs: MongoDB filter arguments
    :return: list of jobs matching the filter criteria
    """
    queue = core4.queue.main.CoreQueue()
    return list(queue.config.sys.queue.find(filter=kwargs))
