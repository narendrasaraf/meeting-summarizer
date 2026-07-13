import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.main import app
from app.routers.meetings import get_queue


class FakeJob:
    def __init__(self, job_id="fake-job-id"):
        self.id = job_id


class FakeQueue:
    def enqueue(self, func, *args, **kwargs):
        # Execute the RQ task synchronously inline for tests
        func(*args, **kwargs)
        return FakeJob()


@pytest.fixture(autouse=True)
def override_rq_queue():
    app.dependency_overrides[get_queue] = lambda: FakeQueue()
    yield
    app.dependency_overrides.pop(get_queue, None)
