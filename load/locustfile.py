"""Load test: many candidates race for one interviewer's only slot.

The setup script exports a short-lived token and resource ids. Every virtual user submits once;
201 means the winner, 409 means PostgreSQL correctly rejected an overlapping write. Both are valid.
"""

import json
import os
from collections import Counter

from gevent.lock import Semaphore
from locust import HttpUser, between, events, task
from locust.exception import StopUser

_candidate_ids = json.loads(os.environ["HIREFLOW_LOAD_CANDIDATE_IDS"])
_next_candidate = 0
_lock = Semaphore()
_statuses: Counter[int] = Counter()


def take_candidate_id() -> str:
    global _next_candidate
    with _lock:
        if _next_candidate >= len(_candidate_ids):
            raise RuntimeError("load user count exceeds prepared candidate count")
        candidate_id = _candidate_ids[_next_candidate]
        _next_candidate += 1
        return candidate_id


class SameSlotSchedulingUser(HttpUser):
    wait_time = between(0.01, 0.02)

    def on_start(self) -> None:
        self.candidate_id = take_candidate_id()
        self.headers = {"Authorization": f"Bearer {os.environ['HIREFLOW_LOAD_TOKEN']}"}

    @task
    def submit_only_once(self) -> None:
        payload = {
            "candidate_id": self.candidate_id,
            "interviewer_id": os.environ["HIREFLOW_LOAD_INTERVIEWER_ID"],
            "job_id": os.environ["HIREFLOW_LOAD_JOB_ID"],
            "starts_at": os.environ["HIREFLOW_LOAD_STARTS_AT"],
            "ends_at": os.environ["HIREFLOW_LOAD_ENDS_AT"],
        }
        with self.client.post("/v1/interviews", json=payload, headers=self.headers, catch_response=True) as response:
            _statuses[response.status_code] += 1
            if response.status_code in (201, 409):
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}: {response.text[:160]}")
        raise StopUser()


@events.quitting.add_listener
def report_business_result(environment, **_: object) -> None:
    successes = _statuses[201]
    conflicts = _statuses[409]
    total = sum(_statuses.values())
    print(f"HireFlow scheduling result: total={total}, created={successes}, conflicts={conflicts}")
    if total and (successes != 1 or conflicts != total - 1):
        print("ERROR: expected exactly one created interview and all other requests to conflict")
        environment.process_exit_code = 1
