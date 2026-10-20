import asyncio
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from interface_cu.browser import open_browser
from interface_cu.contracts import Navigate
from interface_cu.evidence import Evidence
from interface_cu.execution import GuardedExecutor
from interface_cu.handoff import SessionControl
from interface_cu.policy import Policy


@pytest.fixture(scope="session")
def origin():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([sys.executable, "-m", "interface_cu.cli", "serve", "--port", str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with urlopen(address, timeout=0.2) as response:
                    assert response.status == 200
                break
            except (URLError, TimeoutError):
                if process.poll() is not None:
                    pytest.fail("Demo application exited before becoming ready")
                time.sleep(0.05)
        else:
            pytest.fail("Demo application did not become ready")
        yield address
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
async def browser_env(origin, tmp_path):
    evidence = Evidence(tmp_path, "automated_test")
    policy, control = Policy(origin=origin), SessionControl(evidence)
    async with open_browser(policy, control, evidence) as surface:
        executor = GuardedExecutor(surface, policy, control, evidence)
        result = await executor.execute(Navigate(url=origin + "/"))
        assert result.status == "executed"
        yield surface, executor, control, evidence


async def wait_until(predicate, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Expected asynchronous event was not observed")
