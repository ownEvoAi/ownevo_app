"""Benchmark runner Protocol + reference implementations."""

from .m5 import M5BenchmarkRunner, M5PipelineFn, M5PipelineOutput, M5RunArtifacts
from .synthetic import SkillFn, SyntheticBenchmarkRunner, SyntheticTask
from .types import BenchmarkResult, BenchmarkRunner

__all__ = [
    "BenchmarkResult",
    "BenchmarkRunner",
    "M5BenchmarkRunner",
    "M5PipelineFn",
    "M5PipelineOutput",
    "M5RunArtifacts",
    "SkillFn",
    "SyntheticBenchmarkRunner",
    "SyntheticTask",
]
