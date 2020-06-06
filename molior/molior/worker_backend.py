import asyncio

from ..app import logger
from ..tools import write_log
from .backend import Backend
from .notifier import send_mail_notification

from ..model.database import Session
from ..model.build import Build
from ..model.buildtask import BuildTask

backend_queue = asyncio.Queue()


class BackendWorker:
    """
    Backend task

    """

    def __init__(self, task_queue, aptly_queue):
        self.task_queue = task_queue
        self.aptly_queue = aptly_queue

    async def startup_scheduling(self):
        with Session() as session:

            scheduled_builds = session.query(Build).filter(Build.buildstate == "scheduled", Build.buildtype == "deb").all()
            if scheduled_builds:
                for build in scheduled_builds:
                    buildtask = session.query(BuildTask).filter(BuildTask.build == build).first()
                    session.delete(buildtask)
                    await build.set_needs_build()
                session.commit()

            building_builds = session.query(Build).filter(Build.buildstate == "building", Build.buildtype == "deb").all()
            if building_builds:
                for build in building_builds:
                    buildtask = session.query(BuildTask).filter(BuildTask.build == build).first()
                    session.delete(buildtask)
                    await build.set_failed()
                session.commit()

    async def _schedule(self, job):
        b = Backend()
        backend = b.get_backend()
        backend.build(*job)

    async def _started(self, session, build_id):
        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("build_started: no build found for %d", build_id)
            return
        await write_log(build.parent.parent.id, "I: started build %d\n" % build_id)
        await build.set_building()
        session.commit()

    async def _succeeded(self, session, build_id):
        await self.aptly_queue.put({"publish": [build_id]})

    async def _failed(self, session, build_id):
        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("build_failed: no build found for %d", build_id)
            return
        await write_log(build.parent.parent.id, "E: build %d failed\n" % build_id)
        await build.set_failed()
        session.commit()

        buildtask = session.query(BuildTask).filter(BuildTask.build == build).first()
        session.delete(buildtask)
        session.commit()

        # FIXME: do not remove the logs!
        # src_repo = build.buildconfiguration.sourcerepositories[0]
        # for _file in src_repo.path.glob("*_{}*.*".format(build.version)):
        #    logger.info("removing: %s", _file)
        #    os.remove(str(_file))

        if not build.is_ci:
            send_mail_notification(build)

    async def run(self):
        """
        Run the worker task.
        """

        await self.startup_scheduling()

        while True:
            try:
                task = await backend_queue.get()
                if task is None:
                    logger.info("backend:: got emtpy task, aborting...")
                    break

                with Session() as session:
                    handled = False
                    job = task.get("schedule")
                    if job:
                        handled = True
                        await self._schedule(job)
                    build_id = task.get("started")
                    if build_id:
                        handled = True
                        await self._started(session, build_id)
                    build_id = task.get("succeeded")
                    if build_id:
                        handled = True
                        await self._succeeded(session, build_id)
                    build_id = task.get("failed")
                    if build_id:
                        handled = True
                        await self._failed(session, build_id)
                    node_dummy = task.get("node_registered")
                    if node_dummy:
                        # Schedule builds
                        args = {"schedule": []}
                        await self.task_queue.put(args)
                        handled = True

                if not handled:
                    logger.error("backend: got unknown task %s", str(task))

                backend_queue.task_done()

            except Exception as exc:
                logger.exception(exc)

        logger.info("terminating backend task")
