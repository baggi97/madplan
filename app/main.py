"""Indgangspunkt: webserver og ugentlig scheduler i samme proces."""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, flow
from .web import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-16s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("madplan")


def _cron(spec: str) -> CronTrigger:
    ugedag, time = spec.split()
    return CronTrigger(day_of_week=ugedag, hour=int(time), minute=0, timezone=config.TZ)


@asynccontextmanager
async def livscyklus(_app):
    """Starter scheduleren når serveren er oppe, og lukker den ned igen."""
    scheduler = AsyncIOScheduler(timezone=config.TZ)
    scheduler.add_job(flow.hent_forslag, _cron(config.FORSLAG_CRON), id="forslag")
    scheduler.add_job(flow.deadline, _cron(config.DEADLINE_CRON), id="deadline")
    scheduler.start()
    for job in scheduler.get_jobs():
        log.info("Planlagt: %-9s → %s", job.id, job.next_run_time)

    if "--nu" in sys.argv:
        log.info("--nu: henter forslag med det samme")
        asyncio.create_task(flow.hent_forslag(gennemtving=True))

    try:
        yield
    finally:
        # wait=False: et igangværende AI-kald skal ikke holde nedlukningen
        scheduler.shutdown(wait=False)


def main() -> None:
    mangler = config.valider()
    if mangler:
        log.error("Manglende indstillinger: %s", ", ".join(mangler))
        sys.exit(1)

    # Scheduleren hører hjemme her, ikke i web.py, så livscyklussen hægtes på
    # appen nu — før uvicorn.run, altså før nogen ASGI-hændelse er sket.
    app.router.lifespan_context = livscyklus

    log.info("Åbn http://localhost:%d", config.WEB_PORT)
    uvicorn.run(app, host="0.0.0.0", port=config.WEB_PORT, log_level="warning")


if __name__ == "__main__":
    main()
