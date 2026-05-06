"""Benchmark runner Protocol + reference implementations."""

from .labour import LabourBenchmarkError, LabourBenchmarkRunner, LabourCase
from .m5 import M5BenchmarkRunner, M5PipelineFn, M5PipelineOutput, M5RunArtifacts
from .m5_failure_analyzer import (
    M5FailureAnalyzerError,
    M5FailureSnapshot,
    analyze_m5_failures,
    parse_m5_series_id,
)
from .m5_sandbox import M5SandboxError, SandboxedM5BenchmarkRunner
from .synthetic import SkillFn, SyntheticBenchmarkRunner, SyntheticTask
from .types import BenchmarkResult, BenchmarkRunner

__all__ = [
    "BenchmarkResult",
    "BenchmarkRunner",
    "LabourBenchmarkError",
    "LabourBenchmarkRunner",
    "LabourCase",
    "M5BenchmarkRunner",
    "M5FailureAnalyzerError",
    "M5FailureSnapshot",
    "M5PipelineFn",
    "M5PipelineOutput",
    "M5RunArtifacts",
    "M5SandboxError",
    "SandboxedM5BenchmarkRunner",
    "SkillFn",
    "SyntheticBenchmarkRunner",
    "SyntheticTask",
    "analyze_m5_failures",
    "parse_m5_series_id",
]
