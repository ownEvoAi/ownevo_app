"""Benchmark runner Protocol + reference implementations."""

from .labour import LabourBenchmarkError, LabourBenchmarkRunner, LabourCase
from .m5 import M5BenchmarkRunner, M5PipelineFn, M5PipelineOutput, M5RunArtifacts
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
    "M5PipelineFn",
    "M5PipelineOutput",
    "M5RunArtifacts",
    "M5SandboxError",
    "SandboxedM5BenchmarkRunner",
    "SkillFn",
    "SyntheticBenchmarkRunner",
    "SyntheticTask",
]
