"""Canonical AI Lab Assistant - MicroCloud-first AI-powered infrastructure automation."""

__version__ = "0.1.0"
__author__ = "Ismail Kayi"

from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.ai_engine import AIEngine
from lab_ai_assistant.scenarios import MCScenario, SCENARIOS, get_scenario
from lab_ai_assistant.sizing import SizingAdvisor, SizingRecommendation
from lab_ai_assistant.doc_fetcher import DocFetcher

__all__ = [
    "LabOrchestrator",
    "AIEngine",
    "MCScenario",
    "SCENARIOS",
    "get_scenario",
    "SizingAdvisor",
    "SizingRecommendation",
    "DocFetcher",
    "__version__",
]
