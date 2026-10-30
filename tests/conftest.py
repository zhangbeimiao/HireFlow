"""Safe, non-network test configuration established before application modules are imported."""

import os

os.environ.setdefault(
    "HIREFLOW_DATABASE_URL",
    "postgresql+asyncpg://hireflow_test:hireflow_test@127.0.0.1:5432/hireflow_test",
)
os.environ.setdefault("HIREFLOW_JWT_SECRET", "unit-test-secret-with-at-least-thirty-two-bytes")
