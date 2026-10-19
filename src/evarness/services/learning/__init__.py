"""Self-learning subsystem: experiments, signals, and autonomous tuning."""

from evarness.services.learning.engine import LearningEngine, LearningTickReport
from evarness.services.learning.experiment_loop import ExperimentLoop
from evarness.services.learning.models import (
    Experiment,
    ExperimentResult,
    ExperimentStatus,
    IterationRecord,
    MetricSnapshot,
)
from evarness.services.learning.signals import LearningSignalCollector
from evarness.services.learning.store import LearningMetricsStore, MetricAggregate, SignalRecord
from evarness.services.learning.strategies import (
    StrategyDefinition,
    StrategyEvaluation,
    StrategyTrigger,
    StrategyVariant,
    load_strategies,
)

__all__ = [
    "Experiment",
    "ExperimentLoop",
    "ExperimentResult",
    "ExperimentStatus",
    "IterationRecord",
    "LearningEngine",
    "LearningMetricsStore",
    "LearningSignalCollector",
    "LearningTickReport",
    "MetricAggregate",
    "MetricSnapshot",
    "SignalRecord",
    "StrategyDefinition",
    "StrategyEvaluation",
    "StrategyTrigger",
    "StrategyVariant",
    "load_strategies",
]
