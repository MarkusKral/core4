#
# Copyright 2018 Plan.Net Business Intelligence GmbH & Co. KG
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
This module implements :class:`.CoreWorker`. Together with
:mod:`core4.queue.main` and :mod:`core4.queue.process` it delivers a simple
producer/consumer pattern for job execution.

To start a worker from Python goes like this::

    from core4.queue.worker import CoreWorker

    worker = CoreWorker()
    worker.start

To stop the worker start a new Python interpreter and go with::

    from core4.queue.main import CoreQueue

    queue = CoreQueue()
    queue.halt(now=True)

.. note:: use :ref:`coco` to achieve the same with::

    $ coco --worker
    $ coco --halt
"""

import collections
import signal
from datetime import timedelta

import psutil
import pymongo

import core4.queue.job
import core4.queue.process
import core4.queue.query
import core4.service.introspect.main
import core4.util.node
from core4.queue.daemon import CoreDaemon
from core4.service.introspect.command import EXECUTE

#: processing steps in the main loop of :class:`.CoreWorker`
STEPS = (
    "work_jobs",
    "remove_jobs",
    "flag_jobs",
    "collect_stats")


class CoreWorker(CoreDaemon, core4.queue.query.QueryMixin):
    """
    This class is the working horse to carry and execute jobs. Workers have an
    identifier. This identifier defaults to the hostname of the worker and must
    be unique across the cluster.
    """
    kind = "worker"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.offset = None
        self.steps = STEPS
        self.plan = self.create_plan()
        self.cycle.update(dict([(s, 0) for s in self.steps]))
        self.stats_collector = collections.deque(maxlen=
        round(
            self.config.worker.avg_stats_secs
            / self.config.worker.execution_plan.collect_stats))
        # populate with first resource-tuple.
        self.stats_collector.append(
            (min(psutil.cpu_percent(percpu=True)),
             psutil.virtual_memory()[4] / 2. ** 20))
        self.job = None
        self.handle_signal()

    def handle_signal(self):
        # ignore signal from children to avoid defunct zombies
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    def startup(self):
        """
        Implements the **startup** phase of the scheduler. The method is based
        on :class:`.CoreDaemon` implementation and additionally spawns
        :meth:`.collect_job`.
        """
        super().startup()
        intro = core4.service.introspect.main.CoreIntrospector()
        self.job = intro.collect_job()

    def cleanup(self):
        """
        General housekeeping method of the worker.
        """
        ret = self.config.sys.lock.delete_many({"owner": self.identifier})
        self.logger.info(
            "cleanup removed [%d] sys.lock records", ret.raw_result["n"])

    def create_plan(self):
        """
        Creates the worker's execution plan in the main processing loop:

        #. :meth:`.work_jobs` - get next job, inactivate or execute
        #. :meth:`.remove_jobs` - remove jobs
        #. :meth:`.flag_jobs` - flag jobs as non-stoppers, zombies, killed
        #. :meth:`.collect_stats` - collect and save general sever metrics

        :return: dict with step ``name``, ``interval``, ``next`` timestamp
             to execute and method reference ``call``
        """
        plan = []
        now = core4.util.node.now()
        for s in self.steps:
            interval = self.config.worker.execution_plan[s]
            if self.wait_time is None:
                self.wait_time = interval
            else:
                self.wait_time = min(interval, self.wait_time)
            self.logger.debug("set [%s] interval [%1.2f] sec.", s, interval)
            plan.append({
                "name": s,
                "interval": interval,
                "next": now + timedelta(seconds=interval),
                "call": getattr(self, s)
            })
        self.logger.debug(
            "create execution plan with cycle time [%1.2f] sec.",
            self.wait_time)
        return plan

    def run_step(self):
        """
        This method implements the steps of the worker.
        See :meth:`.create_plan` for further details.
        """
        for step in self.plan:
            interval = timedelta(seconds=step["interval"])
            if step["next"] <= self.at:
                self.logger.debug("enter [%s] at cycle [%s]",
                                  step["name"], self.cycle["total"])
                step["call"]()
                self.logger.debug("exit [%s] at cycle [%s]",
                                  step["name"], self.cycle["total"])
                step["next"] = self.at + interval

    def work_jobs(self):
        """
        This method is part of the main
        :meth:`loop <core4.queue.daemon.CoreDaemon.loop>` phase of the worker.

        The step queries and handles the best next job from ``sys.queue`` (see
        :meth:`.get_next_job` and :meth:`.start_job`). Furthermore this method
        *inactivates* jobs.
        """
        doc = self.get_next_job()
        if doc is None:
            return
        if not self.inactivate(doc):
            self.start_job(doc)

    def inactivate(self, doc):
        """
        This method is called by :meth:`.work_jobs` to mark jobs which have
        reached ``inactive_at`` as ``inactive``.

        :param doc: job document to inactivate
        """
        if doc["state"] == core4.queue.job.STATE_DEFERRED:
            if doc.get("inactive_at", None):
                if doc["inactive_at"] <= self.at:
                    update = {
                        "state": core4.queue.job.STATE_INACTIVE
                    }
                    ret = self.config.sys.queue.update_one(
                        filter={"_id": doc["_id"]}, update={"$set": update})
                    if ret.raw_result["n"] != 1:
                        raise RuntimeError(
                            "failed to inactivate job [{}]".format(doc["_id"]))
                    self.queue.unlock_job(doc["_id"])
                    self.queue.make_stat('inactivate_job', str(doc["_id"]))
                    self.logger.error("done execution with [inactive] - [%s] "
                                      "with [%s]", doc["name"], doc["_id"])
                    return True
        return False

    def start_job(self, doc, run_async=True):
        """
        This method is called by :meth:`.work_jobs` and encapsulated for
        testing purposes, only.

        :param doc: job document to launch
        :param run_async: run asynchronous (default) wait for process to
                          complete
        """
        now = self.at
        update = {
            "state": core4.queue.job.STATE_RUNNING,
            "started_at": now,
            "query_at": None,
            "trial": doc["trial"] + 1,
            "locked": {
                "at": now,
                "heartbeat": now,
                "hostname": core4.util.node.get_hostname(),
                "pid": None,
                "worker": self.identifier
            }
        }
        ret = self.config.sys.queue.update_one(
            filter={"_id": doc["_id"]}, update={"$set": update})
        if ret.raw_result["n"] != 1:
            raise RuntimeError(
                "failed to update job [{}] state [starting]".format(
                    doc["_id"]))
        self.queue.make_stat('request_start_job', str(doc["_id"]))
        self.logger.info("launching [%s] with _id [%s]", doc["name"],
                         doc["_id"])
        if run_async:
            core4.service.introspect.main.exec_project(
                doc["name"], EXECUTE, wait=False, job_id=str(doc["_id"]))
        else:
            from core4.queue.process import CoreWorkerProcess
            CoreWorkerProcess().start(doc["_id"], redirect=False, manual=True)

    def get_next_job(self):
        """
        Queries the best next job from collection ``sys.queue``. This method
        filters and orders jobs with the following properties:

        **filter:**

        * not ``locked``
        * with ``attempts_left``
        * in waiting state (``pending``, ``failed`` or ``deferred``)
        * eligable for this or all worker (``.identifier``)
        * not removed, yet (``.removed_at``)
        * not killed, yet (``.killed_at``)
        * with no or past query time (``.query_at``)

        **sort order:**

        * ``.force``
        * ``.priority``
        * enqueue date/time (job ``.id`` sort order)

        The method memorises an ``offset`` attribute to ensure all jobs have a
        chance to get queries across multiple workers. If all jobs have been
        checked, then the offset is reset and querying starts from top.

        In order to handle high priority jobs, the existence of a job *below*
        the current offset is checked. If a job with a higher priority exists
        below the ``offset``, then this high-priority job is returned.

        :return: job document from collection ``sys.queue``
        """
        query = [
            {'locked': None},
            {'attempts_left': {'$gt': 0}},
            {'$or': [
                {'state': s} for s in [
                    core4.queue.job.STATE_PENDING,
                    core4.queue.job.STATE_FAILED,
                    core4.queue.job.STATE_DEFERRED]]
            },
            {'$or': [{'worker': self.identifier},
                     {'worker': None}]},
            {'removed_at': None},
            {'killed_at': None},
            {'$or': [{'query_at': {'$lte': self.at}},
                     {'query_at': None}]},
        ]
        order = [
            ('force', pymongo.DESCENDING),
            ('priority', pymongo.DESCENDING),
            ('_id', pymongo.ASCENDING)
        ]
        if self.offset:
            cur2 = self.config.sys.queue.find(
                filter={'$and': query + [{"_id": {"$lte": self.offset}}]},
                sort=order)
            query.append({'_id': {'$gt': self.offset}})
        else:
            cur2 = None
        cur1 = self.config.sys.queue.find(
            filter={'$and': query}, sort=order)
        while True:
            try:
                data = cur1.next()
            except StopIteration:
                data = None
            if cur2 is not None:
                try:
                    data2 = cur2.next()
                except StopIteration:
                    data2 = None
            else:
                data2 = None
            if data is None:
                if data2 is None:
                    self.offset = None
                    return None
                self.logger.debug(
                    "next job from top chunk [%s]", data2["_id"])
                data = data2
            else:
                self.logger.debug(
                    "next job from bottom chunk [%s]", data["_id"])
                if data2 is not None and data2["priority"] > data["priority"]:
                    data = data2
                    self.logger.debug(
                        "next job from prioritised top chunk [%s]",
                        data["_id"])
            project = data["name"].split(".")[0]
            if self.queue.maintenance(project):
                self.logger.debug(
                    "skipped job [%s] in maintenance", data["_id"])
                continue

            # check system resources
            cur_stats = self.avg_stats()
            if ((cur_stats[0] > self.config.worker.max_cpu)
                    or (cur_stats[1] < self.config.worker.min_free_ram)):
                if not data["force"]:
                    self.logger.info(
                        'skipped job [%s] with _id [%s]: '
                        'not enough resources available: '
                        'cpu [%1.1f], memory [%1.1f]',
                        data["name"], data["_id"], *cur_stats[:2])
                    return None

            # check max_parallel
            count = self.config.sys.queue.count_documents(
                filter={'name': data["name"],
                        "locked.worker": self.identifier})
            if count >= data["max_parallel"]:
                continue
            # acquire lock
            if not self.queue.lock_job(self.identifier, data["_id"]):
                self.logger.debug('skipped job [%s] due to lock failure',
                                  data["_id"])
                continue

            self.offset = data["_id"]
            self.logger.debug('successfully reserved [%s]', data["_id"])
            return data

    def remove_jobs(self):
        """
        This method is part of the main
        :meth:`loop <core4.queue.daemon.CoreDaemon.loop>` phase of the worker.

        The processing step queries all jobs with a specified ``removed_at``
        attribute. After successful job lock, the job is moved from
        ``sys.queue`` into ``sys.journal``.

        .. note:: This method does not unlock the job from ``sys.lock``. This
                  special behavior is required to prevent race conditions
                  between multiple workers simultaneously removing *and*
                  locking the job between ``sys.queue`` and ``sys.lock``.
        """

        cur = self.config.sys.queue.find(
            {"removed_at": {"$ne": None}}
        )
        for doc in cur:
            if self.queue.lock_job(self.identifier, doc["_id"]):
                if self.queue.journal(doc):
                    ret = self.config.sys.queue.delete_one({"_id": doc["_id"]})
                    if ret.raw_result["n"] != 1:
                        raise RuntimeError(
                            "failed to re<move job [{}]".format(doc["_id"]))
                    self.queue.make_stat('remove_job', str(doc["_id"]))
                    self.logger.info(
                        "successfully journaled and removed job [%s]",
                        doc["_id"])
                    # note: we will not unlock the job to prevent race
                    # conditions with other workds; this will be settled
                    # with .cleanup
                    continue
                self.logger.error(
                    "failed to journal and remove job [%s]", doc["_id"])

    def flag_jobs(self):
        """
        This method is part of the main
        :meth:`loop <core4.queue.daemon.CoreDaemon.loop>` phase of the worker.

        The method queries all jobs in state ``running`` locked by the current
        worker and forward processing to

        #. identify and flag non-stopping jobs (see :meth:`.flag_nonstop`),
        #. identify and flag zombies (see :meth:`.flag_zombie`),
        #. identify and handle died jobs (see :meth:`.check_pid`), and to
        #. manage jobs requested to be kill (see :meth:`.kill_pid` and
           :meth:`.check_kill`)
        """
        cur = self.config.sys.queue.find(
            {
                "state": core4.queue.job.STATE_RUNNING,
                "locked.worker": self.identifier
            },
            projection=[
                "_id", "wall_time", "wall_at", "zombie_time", "zombie_at",
                "started_at", "locked.heartbeat", "locked.pid", "killed_at",
                "name"
            ]
        )
        for doc in cur:
            self.flag_nonstop(doc)
            self.flag_zombie(doc)
            self.check_pid(doc)
            self.kill_pid(doc)
        self.check_kill()

    def check_kill(self):
        """
        Identifies jobs requested to be killed in waiting state (``pending``,
        ``deferred`` or ``failed``).

        :param doc: job MongoDB document
        """
        cur = self.config.sys.queue.find(
            {
                "state": {"$in": [
                    core4.queue.job.STATE_PENDING,
                    core4.queue.job.STATE_DEFERRED,
                    core4.queue.job.STATE_FAILED
                ]},
                "killed_at": {
                    "$ne": None
                }
            },
            projection=[
                "_id", "wall_time", "wall_at", "zombie_time", "zombie_at",
                "started_at", "locked.heartbeat", "locked.pid", "killed_at",
                "name"
            ]
        )
        for doc in cur:
            if self.queue.lock_job(self.identifier, doc["_id"]):
                self.kill_pid(doc)

    def flag_nonstop(self, doc):
        """
        Identifies non-stopping jobs which exceeded their runtime beyond the
        specified ``wall_at`` timestamp.

        .. note:: The ``wall_time`` attribute represents the timestamp when
                  the job was flagged. Job execution continues without further
                  action.

        :param doc: job MongoDB document
        """
        if doc["wall_time"] and not doc["wall_at"]:
            if doc["started_at"] < (self.at
                                    - timedelta(seconds=doc["wall_time"])):
                ret = self.config.sys.queue.update_one(
                    filter={
                        "_id": doc["_id"]
                    },
                    update={"$set": {"wall_at": core4.util.node.mongo_now()}})
                if ret.raw_result["n"] == 1:
                    self.logger.warning(
                        "successfully set non-stop job [%s]", doc["_id"])
                self.queue.make_stat('flag_nonstop', str(doc["_id"]))

    def flag_zombie(self, doc):
        """
        Identifies zombie jobs which have not updated their ``heartbeat`` (in
        ``.locked`` attribute) for date/time range specified in the
        ``.zombie_time`` attribute.

        The jobs' :meth:`.progress <core4.queue.job.CoreJob.progress>` method
        updates the ``heartbeat``. Therefore job developers are expected to
        call this method for long-running algorithms regularly.

        .. note:: The ``zombie_at`` attribute represents the timestamp when
                  the job was flagged. Job execution continues without further
                  action.

        :param doc: job MongoDB document
        """
        if not doc["zombie_at"]:
            if doc["locked"]["heartbeat"] < (self.at - timedelta(
                    seconds=doc["zombie_time"])):
                ret = self.config.sys.queue.update_one(
                    filter={
                        "_id": doc["_id"]
                    },
                    update={
                        "$set": {"zombie_at": core4.util.node.mongo_now()}
                    }
                )
                if ret.raw_result["n"] == 1:
                    self.logger.warning(
                        "successfully set zombie job [%s]", doc["_id"])
                self.queue.make_stat('flag_zombie', str(doc["_id"]))

    def check_pid(self, doc):
        """
        Identifies and handles died jobs. If the job PID does not exists, the
        job is flagged ``killed_at`` in ``sys.queue``.

        :param doc: job MongoDB document
        """
        if "locked" in doc and doc["locked"]["pid"] is not None:
            (found, _) = self.pid_exists(doc)
            if not found:
                self.logger.error("pid [%s] not exists, killing",
                                  doc["locked"]["pid"])
                self.queue.exec_kill(doc)

    def kill_pid(self, doc):
        """
        Handles jobs which have been requested to be killed. If the process
        exists, then it is killed and the job state is set to ``killed``.

        :param doc: job MongoDB document
        """
        if doc["killed_at"]:
            (found, proc) = self.pid_exists(doc)
            if found and proc:
                proc.kill()
            self.queue.exec_kill(doc)

    def pid_exists(self, doc):
        """
        Returns ``True`` if the job exists and its OS state is *DEAD* or
        *ZOMBIE*. The :class:`psutil.Process` object is also returned for
        further action.

        :param doc: job MongoDB document
        :return: tuple of ``True`` or ``False`` and the job process or None
        """
        proc = None
        if "locked" in doc and doc["locked"]["pid"] is not None:
            if psutil.pid_exists(doc["locked"]["pid"]):
                proc = psutil.Process(doc["locked"]["pid"])
                if proc.status() not in (psutil.STATUS_DEAD,
                                         psutil.STATUS_ZOMBIE):
                    return True, proc
        return False, proc

    def collect_stats(self):
        """
        Collects cpu and memory, inserts it as tuple into self.stats_collector.
        CPU is computed via CPU-Utilization/(idle-time+io-wait) free RAM is in
        MB.
        """
        # psutil already accounts for idle and io-wait (idle and waiting for
        # IO), we are not interested in both.
        self.stats_collector.append(
            (min(psutil.cpu_percent(percpu=True)),
             psutil.virtual_memory()[4] / 2. ** 20))

    def avg_stats(self):
        """
        :return: tuple of average cpu and memory over the time configured in
                 config.worker.avg_stats_secs.
        """
        cpu = sum(c for c, m in self.stats_collector)
        mem = sum(m for c, m in self.stats_collector)
        div = len(self.stats_collector)
        return cpu / div, mem / div

if __name__ == '__main__':
    import core4.logger.mixin
    core4.logger.mixin.logon()
    w = CoreWorker()
    print("start worker [%s]" % (w.identifier))
    w.start()
