"""Benchmark runner Protocol + reference implementations."""

from .synthetic import SkillFn, SyntheticBenchmarkRunner, SyntheticTask
from .types import BenchmarkResult, BenchmarkRunner

__all__ = [
    "BenchmarkResult",
    "BenchmarkRunner",
    "SkillFn",
    "SyntheticBenchmarkRunner",
    "SyntheticTask",
]
