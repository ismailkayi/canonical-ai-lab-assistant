"""Canonical AI Lab Assistant - MicroCloud-first AI-powered infrastructure automation."""

__version__ = "0.1.0"
__author__ = "Ismail Kayi"

from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.doc_fetcher import DocFetcher
from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.planning import (
    ApprovalManager,
    EnvironmentSnapshot,
    ExecutionPlan,
    PlanValidator,
)
from lab_ai_assistant.scenarios import SCENARIOS, MCScenario, get_scenario
from lab_ai_assistant.sizing import SizingAdvisor, SizingRecommendation

__all__ = [
    "LabOrchestrator",
    "AIEngine",
    "MCScenario",
    "SCENARIOS",
    "get_scenario",
    "SizingAdvisor",
    "SizingRecommendation",
    "DocFetcher",
    "ApprovalManager",
    "EnvironmentSnapshot",
    "ExecutionPlan",
    "PlanValidator",
    "__version__",
]
