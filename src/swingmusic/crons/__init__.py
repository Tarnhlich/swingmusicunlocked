import time
import schedule

from swingmusic.utils.threading import background

@background
def start_cron_jobs(and_exit: bool = False):
    """
    This is the function that triggers the cron jobs.
    """
    from swingmusic.lib.recipes.recents import RecentlyAdded, RecentlyPlayed
    from swingmusic.lib.recipes.topstreamed import TopArtists
    from swingmusic.crons.mixes import Mixes

    # NOTE: RecentlyPlayed is not a CRON job, it's triggered here to
    # populate the values for the very first time.
    RecentlyPlayed()
    RecentlyAdded()

    # Initialized CRON jobs
    TopArtists()
    TopArtists(duration="week")
    Mixes()

    # Trigger all CRON jobs when the app is started.
    schedule.run_all()

    # To manually trigger cron jobs, only once
    if and_exit:
        return

    # Run all CRON jobs on a loop.
    while True:
        schedule.run_pending()
        time.sleep(1)
