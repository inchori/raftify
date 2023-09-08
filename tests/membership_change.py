import asyncio
import json
from concurrent.futures import ProcessPoolExecutor

import pytest

from tests.harness.raft_server import run_raft_cluster, wait_for_until
from tests.utils import RequestType, killall, make_request, reset_fixtures_directory


@pytest.mark.asyncio
async def test_membership_change():
    """ """

    reset_fixtures_directory()
    loop = asyncio.get_running_loop()
    executor = ProcessPoolExecutor()
    loop.run_in_executor(executor, run_raft_cluster, (3))
    await wait_for_until("cluster_size >= 3")

    peers_1: dict = json.loads(make_request(RequestType.GET, 1, "/peers"))
    assert peers_1.keys(), [2]

    killall()
    executor.shutdown()
