"""
Compatibility wrapper for the registry-backed argument extraction node.

The implementation lives in nodes.argument_extraction.hybrid so the graph node
path can stay stable while the extraction internals are split into modules.
"""

from nodes.argument_extraction import (
    ExtractionMerger,
    HybridArgumentExtractor,
    LLMArgumentExtractor,
    MoleculeNameResolver,
    RuleArgumentExtractor,
)
from nodes.argument_extraction.hybrid import extract_arguments_node

__all__ = [
    "ExtractionMerger",
    "HybridArgumentExtractor",
    "LLMArgumentExtractor",
    "MoleculeNameResolver",
    "RuleArgumentExtractor",
    "extract_arguments_node",
]
