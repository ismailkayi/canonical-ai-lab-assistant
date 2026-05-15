"""Canonical AI Lab Assistant - AI-powered infrastructure automation."""

__version__ = "0.1.0"
__author__ = "Ismail Kayi"

from lab_ai_assistant.orchestrator import LabOrchestrator
from lab_ai_assistant.ai_engine import AIEngine

__all__ = ["LabOrchestrator", "AIEngine", "__version__"]
