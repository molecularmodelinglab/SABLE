"""Reusable components for argument extraction."""

import json
import re
from typing import Any, Dict, List, Optional

from schemas.tool_schemas import ArgumentExtractionRequest, ArgumentExtractionResult

try:
    from rdkit import Chem  # type: ignore
    _RDKit_AVAILABLE = True
except Exception:
    Chem = None  # type: ignore
    _RDKit_AVAILABLE = False

try:
    from utils.name_to_smiles import n2s
    _NAME_RESOLVER_AVAILABLE = True
except Exception:
    n2s = None
    _NAME_RESOLVER_AVAILABLE = False
    print("Name-to-SMILES resolver not available; molecule name resolution will be limited.")


def _get_canonical(s):
    if _RDKit_AVAILABLE:
        try:
            mol = Chem.MolFromSmiles(s)
            if mol: return Chem.MolToSmiles(mol)
        except: pass
    return s


def is_likely_smiles(value: str) -> bool:
    """Conservative SMILES validator."""
    if not isinstance(value, str) or not value or len(value) > 200 or " " in value:
        return False

    if re.fullmatch(r"[A-Za-z]+", value):
        return False

    if not re.search(r"[=#@()\[\]\.0-9]", value) and not re.search(r"(Cl|Br)", value):
        return False

    if _RDKit_AVAILABLE:
        try:
            mol = Chem.MolFromSmiles(value, sanitize=False)
            if mol is None:
                return False
            try:
                Chem.SanitizeMol(mol, catchErrors=True)
            except Exception:
                return False
            return True
        except Exception:
            return False

    return bool(re.search(r"^(?:\[.*?\]|Br|Cl|[A-Z][a-z]?|[cnops])+[A-Za-z0-9@+\-\[\]()=#%\.\/\\]*$", value))


def assess_llm_extraction_quality(extracted: Dict[str, Any], original_prompt: str) -> float:
    """Assess extraction completeness and basic structural quality."""
    confidence = 1.0

    if not extracted.get("target_properties"):
        confidence *= 0.5
    if not extracted.get("starting_molecules") and "molecule" not in original_prompt.lower():
        confidence *= 0.8

    confidence *= 1.0 if extracted.get("max_iterations") else 0.9
    confidence *= 1.0 if extracted.get("batch_size") else 0.95

    if extracted.get("target_properties"):
        for target_property in extracted["target_properties"]:
            if not all(key in target_property for key in ["property_name", "optimization_mode"]):
                confidence *= 0.7
                break

    return max(0.0, min(1.0, confidence))


def argument_result_from_dict(parsed: Dict[str, Any], default_method: str) -> ArgumentExtractionResult:
    method = (
        parsed.get("extraction_method")
        or parsed.get("used_method")
        or default_method
    )
    return ArgumentExtractionResult(
        parsed_arguments=parsed,
        starting_molecules=list(parsed.get("starting_molecules") or []),
        target_properties=list(parsed.get("target_properties") or []),
        proteins=list(parsed.get("proteins") or []),
        confidence_score=float(parsed.get("confidence_score") or 0.0),
        method=str(method),
        metadata={
            key: value
            for key, value in parsed.items()
            if key not in {"starting_molecules", "target_properties", "proteins"}
        },
    )


class MoleculeNameResolver:
    """Resolves molecule names into SMILES strings."""

    def resolve(self, name: str) -> Optional[str]:
        if _NAME_RESOLVER_AVAILABLE and n2s:
            try:
                print(f"🔍 Resolving molecule name: {name}")
                smiles = n2s(name)
                if smiles:
                    print(f"✓ Resolved '{name}' to SMILES: {smiles}")
                    return smiles
                print(f"⚠️  Could not resolve '{name}' via name resolution service")
            except Exception as e:
                print(f"⚠️  Error resolving '{name}': {e}")

        molecule_names = {
            "aspirin": "CC(=O)OC1=CC=CC=C1C(=O)O",
            "caffeine": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
            "ibuprofen": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O",
            "paracetamol": "CC(=O)NC1=CC=C(C=C1)O",
            "acetaminophen": "CC(=O)NC1=CC=C(C=C1)O",
            "ciprofloxacin": "C1CC1N2C=C(C(=O)C3=CC(=C(C=C32)N4CCNCC4)F)C(=O)O",
        }

        resolved = molecule_names.get(name.lower())
        if resolved:
            print(f"✓ Resolved '{name}' from hardcoded dictionary: {resolved}")
        else:
            print(f"⚠️  Could not resolve molecule name: {name}")

        return resolved


class LLMArgumentExtractor:
    """Extracts workflow arguments from an LLM client response."""

    def __init__(self, llm_client=None, quality_assessor=None, **_: Any):
        self.llm_client = llm_client
        self.quality_assessor = quality_assessor

    def extract(self, request: ArgumentExtractionRequest) -> ArgumentExtractionResult:
        parsed = self.extract_dict(request.prompt) or {}
        return argument_result_from_dict(parsed, default_method="llm")

    def extract_dict(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.llm_client:
            return None

        try:
            response = self.llm_client.generate(prompt=prompt)
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                extracted_data = json.loads(json_match.group())

                llm_self_confidence = extracted_data.get("llm_confidence", None)
                if llm_self_confidence is not None:
                    llm_self_confidence = float(llm_self_confidence)

                quality_confidence = (
                    self.quality_assessor(extracted_data, prompt)
                    if self.quality_assessor
                    else assess_llm_extraction_quality(extracted_data, prompt)
                )

                extracted_data["llm_self_confidence"] = (
                    llm_self_confidence if llm_self_confidence is not None else quality_confidence
                )
                extracted_data["extraction_quality"] = quality_confidence
                extracted_data["extraction_method"] = "llm"

                if llm_self_confidence is not None:
                    extracted_data["confidence_score"] = min(llm_self_confidence, quality_confidence)
                else:
                    extracted_data["confidence_score"] = quality_confidence

                return extracted_data
        except Exception as e:
            print(f"LLM extraction failed: {e}")
            return None

        return None


class RuleArgumentExtractor:
    """Runs the deterministic rule-based argument extractor."""

    def __init__(self, legacy_extractor=None, molecule_resolver: Optional[MoleculeNameResolver] = None, **_: Any):
        self.legacy_extractor = legacy_extractor
        self.molecule_resolver = molecule_resolver or MoleculeNameResolver()

    def extract(self, request: ArgumentExtractionRequest) -> ArgumentExtractionResult:
        parsed = self.extract_dict(request.prompt)
        return argument_result_from_dict(parsed, default_method="rule_based")

    def extract_dict(self, prompt: str) -> Dict[str, Any]:
        if self.legacy_extractor:
            extractor = self.legacy_extractor
        else:
            from nodes.argument_extraction.hybrid import HybridArgumentExtractor

            extractor = HybridArgumentExtractor(
                llm_client=None,
                molecule_resolver=self.molecule_resolver,
                configure_components=False,
            )
        return extractor.extract_with_rules(prompt)


class ExtractionMerger:
    """Merges LLM extraction with deterministic rule fallbacks."""

    def __init__(self, legacy_extractor=None, **_: Any):
        self.legacy_extractor = legacy_extractor

    def merge(
        self,
        llm_result: Optional[Dict[str, Any]],
        rule_result: Dict[str, Any],
        original_prompt: str,
    ) -> Dict[str, Any]:
        if self.legacy_extractor:
            extractor = self.legacy_extractor
        else:
            from nodes.argument_extraction.hybrid import HybridArgumentExtractor

            extractor = HybridArgumentExtractor(llm_client=None, configure_components=False)
        return extractor.validate_and_merge(llm_result, rule_result, original_prompt)


_is_likely_smiles = is_likely_smiles
_argument_result_from_dict = argument_result_from_dict
