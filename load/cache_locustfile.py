"""Compare PostgreSQL list reads with warmed Redis Cache Aside reads."""

import os

from locust import HttpUser, between, task


class RecruitingListUser(HttpUser):
    wait_time = between(0.01, 0.03)

    def on_start(self) -> None:
        self.headers = {"Authorization": f"Bearer {os.environ['HIREFLOW_CACHE_LOAD_TOKEN']}"}
        self.expected_cache = "HIT"
        if os.environ.get("HIREFLOW_CACHE_LOAD_MODE") == "database":
            self.headers["Cache-Control"] = "no-cache"
            self.expected_cache = "BYPASS"

    def get_list(self, path: str) -> None:
        with self.client.get(path, headers=self.headers, catch_response=True) as response:
            outcome = response.headers.get("X-Cache")
            if response.status_code != 200:
                response.failure(f"unexpected HTTP {response.status_code}: {response.text[:160]}")
            elif outcome != self.expected_cache:
                response.failure(f"expected X-Cache {self.expected_cache}, got {outcome}")
            else:
                response.success()

    @task
    def list_jobs(self) -> None:
        self.get_list("/v1/jobs")

    @task
    def list_candidates(self) -> None:
        self.get_list("/v1/candidates")
