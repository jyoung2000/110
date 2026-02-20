"""Async job queue for managing scrape jobs."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from src.utils.paths import data_path
from src.utils.logging import setup_logging

logger = setup_logging("jobs")

JOBS_PATH = data_path("config", "jobs.json")


class JobQueue:
    """Manages scrape job history and provides access to current/past jobs."""

    def __init__(self):
        self._jobs: list[dict] = []
        self._loaded = False

    def load(self):
        try:
            if JOBS_PATH.exists():
                with open(JOBS_PATH, "r") as f:
                    self._jobs = json.load(f)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Failed to load jobs: {e}")
            self._jobs = []
            self._loaded = True

    def save(self):
        try:
            JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(JOBS_PATH, "w") as f:
                json.dump(self._jobs[-200:], f, indent=1, default=str)
        except Exception as e:
            logger.error(f"Failed to save jobs: {e}")

    def add_job(self, job: dict):
        if not self._loaded:
            self.load()
        self._jobs.append(job)
        self.save()

    def update_job(self, job_id: str, data: dict):
        if not self._loaded:
            self.load()
        for j in self._jobs:
            if j.get("id") == job_id:
                j.update(data)
                self.save()
                return

    def get_all(self) -> list[dict]:
        if not self._loaded:
            self.load()
        return list(reversed(self._jobs[-200:]))

    def get_job(self, job_id: str) -> Optional[dict]:
        if not self._loaded:
            self.load()
        for j in self._jobs:
            if j.get("id") == job_id:
                return j
        return None

    def delete_job(self, job_id: str) -> bool:
        if not self._loaded:
            self.load()
        initial = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.get("id") != job_id]
        if len(self._jobs) < initial:
            self.save()
            return True
        return False


job_queue = JobQueue()
