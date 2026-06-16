"""
Hybrid Decision Engine — Analyzers Package
==========================================
Each analyzer is an independent module that receives OHLCV data and returns
a standardized analysis result dict.
"""
from .base import BaseAnalyzer, AnalysisResult
