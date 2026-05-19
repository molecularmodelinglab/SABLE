"""
Extract and parse arguments from user prompt using hybrid LLM + rule-based approach.
"""

import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from schemas.state import (
    WorkflowState,
    TargetProperty,
    OptimizationMode,
    MoleculeSource,
    ProteinTarget,
)
from schemas.errors import NodeError
from schemas.properties import get_property_catalog
from schemas.tool_registry import ToolKind, ToolRunRecord, ToolSpec
from schemas.tool_schemas import ArgumentExtractionRequest, ArgumentExtractionResult
from tools.registry import ToolRegistry, get_tool_registry
from nodes.argument_extraction.components import (
    ExtractionMerger,
    LLMArgumentExtractor,
    MoleculeNameResolver,
    RuleArgumentExtractor,
    _argument_result_from_dict,
    _is_likely_smiles,
)


class HybridArgumentExtractor:
    """Combines LLM-based extraction with rule-based validation and fallbacks."""
    PROPERTY_CATALOG = get_property_catalog()
    PROPERTY_BOUNDS = {
        prop_id: spec.default_bounds
        for prop_id, spec in PROPERTY_CATALOG.properties.items()
        if spec.default_bounds is not None
    }

    AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWYBXZJUO")

    def __init__(
        self,
        llm_client=None,
        molecule_resolver: Optional[MoleculeNameResolver] = None,
        llm_extractor: Optional[LLMArgumentExtractor] = None,
        rule_extractor: Optional[RuleArgumentExtractor] = None,
        merger: Optional[ExtractionMerger] = None,
        configure_components: bool = True,
        **_: Any,
    ):
        self.llm_client = llm_client
        self.molecule_resolver = molecule_resolver or MoleculeNameResolver()
        if configure_components:
            self.llm_extractor = llm_extractor or LLMArgumentExtractor(
                llm_client=llm_client,
                quality_assessor=self._assess_llm_extraction_quality,
            )
            self.rule_extractor = rule_extractor or RuleArgumentExtractor(
                legacy_extractor=self,
                molecule_resolver=self.molecule_resolver,
            )
            self.merger = merger or ExtractionMerger(legacy_extractor=self)
        else:
            self.llm_extractor = llm_extractor
            self.rule_extractor = rule_extractor
            self.merger = merger

    def extract(self, request: ArgumentExtractionRequest) -> ArgumentExtractionResult:
        final_result = self.extract_dict(request.prompt)
        return _argument_result_from_dict(final_result, default_method="hybrid")

    def extract_dict(self, prompt: str) -> Dict[str, Any]:
        llm_result = self.extract_with_llm(prompt)
        rule_result = self.extract_with_rules(prompt)
        if self.merger:
            return self.merger.merge(llm_result, rule_result, prompt)
        return self.validate_and_merge(llm_result, rule_result, prompt)

    def extract_with_llm(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Extract arguments using LLM with structured output."""
        if self.llm_extractor:
            return self.llm_extractor.extract_dict(prompt)
        return LLMArgumentExtractor(
            llm_client=self.llm_client,
            quality_assessor=self._assess_llm_extraction_quality,
        ).extract_dict(prompt)
    
    def _assess_llm_extraction_quality(self, extracted: Dict[str, Any], original_prompt: str) -> float:
        """
        Assess the quality of LLM extraction based on completeness and correctness.
        This is our independent assessment, separate from the LLM's self-confidence.
        """
        confidence = 1.0
        
        # Penalize missing critical fields
        if not extracted.get('target_properties'):
            confidence *= 0.5
        if not extracted.get('starting_molecules') and 'molecule' not in original_prompt.lower():
            confidence *= 0.8
        
        # Reward completeness
        if extracted.get('max_iterations'):
            confidence *= 1.0
        else:
            confidence *= 0.9
        
        if extracted.get('batch_size'):
            confidence *= 1.0
        else:
            confidence *= 0.95
        
        # Check if target properties have required fields
        if extracted.get('target_properties'):
            for tp in extracted['target_properties']:
                if not all(k in tp for k in ['property_name', 'optimization_mode']):
                    confidence *= 0.7
                    break
        
        return max(0.0, min(1.0, confidence))
    
    def extract_with_rules(self, prompt: str) -> Dict[str, Any]:
        """Fallback rule-based extraction using existing logic."""
        return self._legacy_extract(prompt)
    
    def _legacy_extract(self, prompt: str) -> Dict[str, Any]:
        """Existing regex-based extraction logic kept as the rule fallback."""
        prompt_lower = prompt.lower()
        parsed = {}
        
        # look for SMILES patterns or molecule names
        smiles_pattern = r"(?:\[.*?\]|Br|Cl|[A-Z][a-z]?|[cnops])[A-Za-z0-9@+\-\[\]()=#%\.]*"
        smiles_matches = re.findall(smiles_pattern, prompt)
        candidates = [s for s in smiles_matches if _is_likely_smiles(s)]
        
        # Deduplicate while preserving order
        seen = set()
        potential_smiles = []
        for s in candidates:
            if s not in seen:
                seen.add(s)
                potential_smiles.append(s)
        
        if potential_smiles:
            parsed['starting_molecules'] = potential_smiles[:3]
        
        molecule_names = self._extract_molecule_names_from_prompt(prompt)
        
        for name in molecule_names:
            # resolve each potential molecule name
            resolved_smiles = self._resolve_molecule_name(name)
            if resolved_smiles:
                parsed.setdefault('starting_molecules', [])
                if resolved_smiles not in parsed['starting_molecules']:
                    parsed['starting_molecules'].append(resolved_smiles)
                    print(f"✓ Found and resolved molecule name '{name}' in prompt")

        # extract protein targets (sequences or UniProt IDs)
        proteins: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()

        # UniProt ID patterns (capture tokens like P12345, Q8N158, etc.)
        uniprot_pattern = re.compile(r"uniprot(?:\s+id)?[\s:=]+([A-Za-z0-9\-]{5,12})", re.IGNORECASE)
        for match in uniprot_pattern.findall(prompt):
            uid = match.strip().upper()
            if re.fullmatch(r"[A-Z0-9\-]{5,12}", uid) and uid not in seen_keys:
                seen_keys.add(uid)
                proteins.append({'chain_id': None, 'uniprot_id': uid})

        # Sequence blocks following keywords like "protein sequence"
        sequence_pattern = re.compile(
            r"(?:(protein|target)\s*)?(sequence|seq)\s*(?:for\s*chain\s*([A-Za-z0-9]+))?\s*[:=\-]\s*([A-Za-z\s]{20,})",
            re.IGNORECASE,
        )
        for match in sequence_pattern.finditer(prompt):
            chain_hint = match.group(3)
            seq_raw = match.group(4)
            seq = ''.join(seq_raw.split()).upper()
            if len(seq) < 20 or not set(seq) <= self.AMINO_ACIDS:
                continue
            key = f"SEQ:{seq}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            proteins.append({
                'chain_id': chain_hint.upper() if chain_hint else None,
                'sequence': seq
            })

        # Assign default chain IDs and filter out empty entries
        normalized_proteins: List[Dict[str, Any]] = []
        for idx, protein in enumerate(proteins):
            if not protein.get('sequence') and not protein.get('uniprot_id'):
                continue
            chain_id = protein.get('chain_id')
            if not chain_id:
                chain_id = chr(ord('A') + idx)
            normalized = {
                'chain_id': chain_id,
                'sequence': protein.get('sequence'),
                'uniprot_id': protein.get('uniprot_id')
            }
            normalized_proteins.append(normalized)

        if normalized_proteins:
            parsed['proteins'] = normalized_proteins
        
        # Extract target properties
        property_keywords = self.PROPERTY_CATALOG.parser_keyword_map()
        
        targets = []
        for prop, keywords in property_keywords.items():
            if any(kw in prompt_lower for kw in keywords):
                mode = OptimizationMode.MAXIMIZE
                if prop == 'toxicity' and any(word in prompt_lower for word in ['reduce', 'minimize', 'lower', 'decrease']):
                    mode = OptimizationMode.MINIMIZE
                elif prop == 'molecular_weight' and any(word in prompt_lower for word in ['reduce', 'minimize', 'lower', 'small']):
                    mode = OptimizationMode.MINIMIZE

                spec = self.PROPERTY_CATALOG.get(prop)
                if mode == OptimizationMode.MATCH:
                    transformation = "TRIANGULAR"
                elif mode in [OptimizationMode.MAXIMIZE, OptimizationMode.MINIMIZE]:
                    transformation = spec.default_transformation if spec else "LINEAR"
                
                
                targets.append({
                    'property_name': prop,
                    'optimization_mode': mode.value,
                    'bounds': self.PROPERTY_CATALOG.bounds_for(prop),
                    'transformation': transformation
                })
        
        # Assign equal weights to all targets
        if targets:
            equal_weight = 1.0 / len(targets)
            for target in targets:
                target['weight'] = equal_weight
        
        parsed['target_properties'] = targets
        
        # Extract iteration/budget constraints
        iteration_match = re.search(r'(\d+)\s*(?:iterations?|rounds?|cycles?)', prompt_lower)
        if iteration_match:
            parsed['max_iterations'] = min(int(iteration_match.group(1)), 100)
        else:
            parsed['max_iterations'] = 10
        
        # Extract batch size
        batch_match = re.search(r'batch\s*(?:size)?\s*(?:of)?\s*(\d+)', prompt_lower)
        if batch_match:
            parsed['batch_size'] = min(int(batch_match.group(1)), 20)
        else:
            parsed['batch_size'] = 5
        
        # Determine molecule source strategy
        if 'enumerate' in prompt_lower or 'analogs' in prompt_lower or 'derivatives' in prompt_lower:
            parsed['molecule_source'] = MoleculeSource.ENUMERATED.value
        elif 'library' in prompt_lower or 'database' in prompt_lower or 'screen' in prompt_lower:
            parsed['molecule_source'] = MoleculeSource.EXTERNAL_LIBRARY.value
        elif parsed.get('starting_molecules'):
            parsed['molecule_source'] = MoleculeSource.PROVIDED.value
        else:
            parsed['molecule_source'] = MoleculeSource.GENERATED.value

        healer_mode = None
        if 'fragment' in prompt_lower or '.' in prompt and re.search(r"\w+\.\w+", prompt):
            healer_mode = 'FragmentHEALER'
        elif any(k in prompt_lower for k in ['r-group', 'r group', 'rgroups', 'r-groups', 'attach r', 'grow', 'vary', 'fix the', 'fix']) and any(k in prompt_lower for k in ['side chain', 'side-chain', 'scaffold', 'attach', 'r group', 'r-group', 'imidzole', 'imidazole', 'grow my small molecule', 'r group analogs']):
            healer_mode = 'SiteHEALER'
        else:
            healer_mode = 'MoleculeHEALER'

        parsed['healer_mode'] = healer_mode

        # Extract enumeration size if mentioned
        enum_match = re.search(r'(\d+)\s*(?:molecules?|compounds?|analogs?|derivatives?)', prompt_lower)
        if enum_match:
            parsed['enumeration_size'] = min(int(enum_match.group(1)), 1000)
        else:
            parsed['enumeration_size'] = 100
        
        # Calculate rule-based confidence score based on what was extracted
        rule_confidence = 0.5  # Base confidence for rule-based
        
        # Increase confidence if we found key information
        if parsed.get('starting_molecules'):
            rule_confidence += 0.15
        if parsed.get('target_properties') and len(parsed['target_properties']) > 0:
            rule_confidence += 0.15
        if parsed.get('proteins'):
            rule_confidence += 0.1
        if iteration_match:  # Explicitly mentioned iterations
            rule_confidence += 0.05
        if batch_match:  # Explicitly mentioned batch size
            rule_confidence += 0.05
        if enum_match:  # Explicitly mentioned enumeration size
            rule_confidence += 0.05
        
        parsed['extraction_method'] = 'rule_based'
        parsed['rule_confidence'] = min(0.7, rule_confidence)  # Cap at 0.7 for rule-based
        parsed['confidence_score'] = parsed['rule_confidence']  # Overall confidence equals rule confidence
        
        return parsed
    
    def _extract_molecule_names_from_prompt(self, prompt: str) -> List[str]:
        """
        Extract potential molecule names from the prompt using context-aware detection.
        Avoids matching common verbs and instructions.
        Returns a list of candidate molecule names found.
        """

        exclude_words = {
            'make', 'optimize', 'find', 'improve', 'generate', 'create', 'design',
            'enumerate', 'maximize', 'minimize', 'increase', 'decrease', 'reduce',
            'maintain', 'ensure', 'target', 'focus', 'explore', 'discover', 'search',
            'starting', 'better', 'higher', 'lower', 'good', 'best', 'worst',
            'molecule', 'compound', 'drug', 'chemical', 'analog', 'derivative',
            'structure', 'smiles', 'property', 'properties', 'adme', 'bioavailability'
        }
        
        candidates = []
        lower_prompt = prompt.lower()
        
        common_drugs = [
            'aspirin', 'caffeine', 'ibuprofen', 'paracetamol', 'acetaminophen',
            'ciprofloxacin', 'metformin', 'warfarin', 'sildenafil', 'atorvastatin',
            'omeprazole', 'metoprolol', 'fluoxetine', 'sertraline', 'diazepam',
            'captopril', 'losartan', 'tamoxifen', 'methotrexate', 'taxol',
            'propranolol', 'ranitidine', 'levothyroxine', 'ketoconazole', 'acyclovir',
            'diclofenac', 'morphine', 'furosemide', 'astemizole', 'nicotine',
            'indomethacin', 'ethanol', 'diphenhydramine', 'paclitaxel', 'vancomycin',
            'cisplatin', 'doxorubicin', 'risperidone', 'amiodarone', 'chloroquine',
            'tetracycline', 'cyclosporine', 'penicillin', 'cephalexin', 'cefuroxime',
            'dasatinib', 'gefitinib', 'sorafenib', 'benzene', 'imatinib',
            'venetoclax', 'aripiprazole', 'oseltamivir', 'erlotinib', 'vemurafenib',
            'ritonavir', 'resveratrol', 'curcumin', 'epicatechin', 'quercetin',
            'fentanyl', 'atenolol', 'celecoxib', 'erythromycin'
        ]
        
        # check for known drugs in the prompt (most reliable)
        for drug in common_drugs:
            if drug in lower_prompt:
                candidates.append(drug)
        
        # Look for capitalized words, but with context and exclusions
        # Only consider words that appear after certain keywords
        context_patterns = [
            r'(?:starting from|start with|optimize|of)\s+([A-Z][a-z]{3,}[a-z]*)',
            r'([A-Z][a-z]{3,}[a-z]*)\s+(?:analog|derivative|structure)',
            r'improve\s+(?:the\s+)?([A-Z][a-z]{3,}[a-z]*)',
        ]
        
        for pattern in context_patterns:
            matches = re.findall(pattern, prompt)
            for match in matches:
                if match.lower() not in exclude_words:
                    candidates.append(match)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_candidates = []
        for name in candidates:
            name_lower = name.lower()
            if name_lower not in seen and name_lower not in exclude_words:
                seen.add(name_lower)
                unique_candidates.append(name)
        
        return unique_candidates
        
        return unique_candidates

    def validate_and_merge(self, llm_result: Optional[Dict[str, Any]], 
                          rule_result: Dict[str, Any], 
                          original_prompt: str) -> Dict[str, Any]:
        """
        Validate LLM results and merge with rule-based fallbacks.
        Uses LLM-provided molecule names (provided_molecule_names) to verify SMILES via n2s().
        This prevents LLM hallucinations by validating against authoritative chemical databases.
        
        Flow:
        1. If LLM provides molecule names → verify via n2s() and use n2s() SMILES
        2. If LLM provides SMILES directly (no names) → validate structure, use as-is
        3. If validation fails → fall back to rule-based extraction
        """
        
        # Decision: Use rule-based if LLM failed or has very low confidence
        if not llm_result or llm_result.get('confidence_score', 0) < 0.3:
            reason = "LLM unavailable" if not llm_result else f"low LLM confidence ({llm_result.get('confidence_score', 0):.2f})"
            print(f"✓ Using rule-based extraction ({reason})")
            rule_result['fallback_reason'] = reason
            return rule_result
        
        print(f"✓ Using LLM extraction (confidence: {llm_result.get('confidence_score', 0):.2f})")
        merged = llm_result.copy()
        merged['used_method'] = 'llm'
        
        # Track any supplements from rule-based
        supplements = []
        

        llm_provided_names = merged.get('provided_molecule_names', [])
        
        if llm_provided_names:
            print(f"✓ LLM identified molecule names: {llm_provided_names}")
            print(f"🔍 Verifying SMILES via n2s() database...")
            
            # get authoritative SMILES from PubChem
            authoritative_smiles = []
            failed_names = []
            
            for name in llm_provided_names:
                resolved = self._resolve_molecule_name(name)
                if resolved:
                    authoritative_smiles.append(resolved)
                    print(f"   ✓ {name} → {resolved[:50]}{'...' if len(resolved) > 50 else ''}")
                else:
                    failed_names.append(name)
                    print(f"   ✗ Could not resolve '{name}' via n2s()")
            
            # Use n2s() results as ground truth
            validated_smiles = authoritative_smiles
            
            # Check if LLM provided different SMILES (potential hallucination)
            llm_smiles = merged.get('starting_molecules', [])
            if llm_smiles and authoritative_smiles:

                llm_smiles_set = set(llm_smiles)
                auth_smiles_set = set(authoritative_smiles)
                
                if llm_smiles_set != auth_smiles_set:
                    print(f"⚠️  LLM SMILES differs from n2s() database")
                    print(f"   Using n2s() results as authoritative source")
                    supplements.append("Verified SMILES via n2s() - replaced LLM SMILES with database values")
                else:
                    print(f"✓ LLM SMILES matches n2s() database - no hallucination detected")
            
            # If some names failed to resolve, try to use LLM SMILES for those
            if failed_names and llm_smiles:
                print(f"⚠️  Couldn't resolve {len(failed_names)} names via n2s(), using LLM SMILES as fallback")
                for smiles in llm_smiles:
                    if smiles not in validated_smiles and _is_likely_smiles(smiles):
                        validated_smiles.append(smiles)
                        supplements.append(f"Used LLM SMILES for unresolvable molecule name")
        
        else:
            # No molecule names provided - user likely gave SMILES directly
            print(f"ℹ️  No molecule names provided - assuming direct SMILES input")
            validated_smiles = []
            llm_smiles = merged.get('starting_molecules', [])
            
            for smiles in llm_smiles:
                if _is_likely_smiles(smiles):
                    validated_smiles.append(smiles)
                    print(f"   ✓ Validated SMILES: {smiles[:50]}{'...' if len(smiles) > 50 else ''}")
                else:
                    # Not a valid SMILES - might be a molecule name that LLM missed
                    print(f"⚠️  '{smiles}' doesn't look like SMILES, trying n2s()...")
                    resolved = self._resolve_molecule_name(smiles)
                    if resolved:
                        validated_smiles.append(resolved)
                        supplements.append(f"Resolved molecule name '{smiles}' to SMILES")
                    else:
                        print(f"   ✗ Could not resolve '{smiles}' - skipping")
        
        # Add any SMILES found by rules that weren't captured above
        rule_smiles_added = 0
        for smiles in rule_result.get('starting_molecules', []):
            if smiles not in validated_smiles:
                validated_smiles.append(smiles)
                rule_smiles_added += 1
        
        if rule_smiles_added > 0:
            supplements.append(f"{rule_smiles_added} additional SMILES from rule-based extraction")
            validated_smiles.append(smiles)
            rule_smiles_added += 1
        
        if rule_smiles_added > 0:
            supplements.append(f"{rule_smiles_added} SMILES from rules")
        
        merged['starting_molecules'] = validated_smiles

        # Normalize and merge protein targets
        llm_proteins = self._normalize_protein_list(merged.get('proteins'))
        rule_proteins = self._normalize_protein_list(rule_result.get('proteins'))

        if not llm_proteins and rule_proteins:
            merged['proteins'] = rule_proteins
            supplements.append("proteins from rules")
        elif llm_proteins:
            supplemented = 0
            existing_keys = {
                ('SEQ', p['sequence']) if p.get('sequence') else ('UNIPROT', p.get('uniprot_id'))
                for p in llm_proteins
            }
            for protein in rule_proteins:
                key = ('SEQ', protein.get('sequence')) if protein.get('sequence') else ('UNIPROT', protein.get('uniprot_id'))
                if key not in existing_keys:
                    llm_proteins.append(protein)
                    existing_keys.add(key)
                    supplemented += 1
            if supplemented:
                supplements.append(f"{supplemented} proteins from rules")
            merged['proteins'] = llm_proteins
        else:
            merged['proteins'] = []
        
        # Cross-validate and supplement target properties
        if not merged.get('target_properties') and rule_result.get('target_properties'):
            merged['target_properties'] = rule_result['target_properties']
            merged['confidence_score'] = merged.get('confidence_score', 1.0) * 0.8
            supplements.append("target_properties from rules")
        elif merged.get('target_properties'):
            # LLM extracted properties - supplement with bounds and transformations from rules
            bounds_added = 0
            bounds_fixed = 0
            weights_fixed = 0
            
            for prop in merged['target_properties']:
                prop_name = self.PROPERTY_CATALOG.normalize(prop.get('property_name', ''))
                
                # Add bounds if completely missing
                default_bounds = self.PROPERTY_CATALOG.bounds_for(prop_name)
                if not prop.get('bounds') and default_bounds:
                    prop['bounds'] = default_bounds
                    bounds_added += 1
                elif prop.get('bounds'):
                    # Fix partial bounds (e.g., [None, 60] or [2, None])
                    bounds = prop['bounds']
                    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                        lower, upper = bounds
                        # Replace None with default bounds
                        if lower is None and default_bounds:
                            lower = default_bounds[0]
                            bounds_fixed += 1
                        elif lower is None:
                            lower = 0.0  # Fallback if no default bounds
                            bounds_fixed += 1
                        
                        if upper is None and default_bounds:
                            upper = default_bounds[1]
                            bounds_fixed += 1
                        elif upper is None:
                            upper = 1000.0  # Fallback if no default bounds
                            bounds_fixed += 1
                        
                        # Ensure bounds is a tuple (not list) for consistency
                        prop['bounds'] = (float(lower), float(upper))
                
                # add transformation if missing
                if not prop.get('transformation'):
                    if prop['optimization_mode'] == OptimizationMode.MATCH:
                        prop['transformation'] = "TRIANGULAR"
                    elif prop['optimization_mode'] in [OptimizationMode.MAXIMIZE, OptimizationMode.MINIMIZE]:
                        prop['transformation'] = "LINEAR"

            # Normalize weights to ensure they sum to 1.0 and are equal
            num_props = len(merged['target_properties'])
            if num_props > 0:
                equal_weight = 1.0 / num_props
                for prop in merged['target_properties']:
                    if not prop.get('weight') or prop['weight'] != equal_weight:
                        prop['weight'] = equal_weight
                        weights_fixed += 1
            
            if bounds_added > 0:
                supplements.append(f"bounds for {bounds_added} properties")
            if bounds_fixed > 0:
                supplements.append(f"fixed partial bounds for {bounds_fixed} values")
            if weights_fixed > 0:
                supplements.append(f"equal weights for {num_props} properties")
        
        # Sanity check numerical values
        if merged.get('max_iterations', 0) > 100:
            merged['max_iterations'] = min(merged['max_iterations'], 100)
        if merged.get('batch_size', 0) > 50:
            merged['batch_size'] = min(merged['batch_size'], 50)
        
        # Ensure defaults
        merged.setdefault('max_iterations', 10)
        merged.setdefault('batch_size', 5)
        merged.setdefault('enumeration_size', 100)
        merged.setdefault('confidence_score', 0.5)
        
        # Record what was supplemented
        if supplements:
            merged['rule_supplements'] = supplements
            print(f"  → Supplemented with: {', '.join(supplements)}")
        
        return merged
    
    def _resolve_molecule_name(self, name: str) -> Optional[str]:
        """
        Resolve molecule names to SMILES using name_to_smiles service.
        Falls back to hardcoded common molecules if service is unavailable.
        """
        if self.molecule_resolver:
            return self.molecule_resolver.resolve(name)

        # First try the name resolution service (PubChem-backed)
        if _NAME_RESOLVER_AVAILABLE and n2s:
            try:
                print(f"🔍 Resolving molecule name: {name}")
                smiles = n2s(name)
                if smiles:
                    print(f"✓ Resolved '{name}' to SMILES: {smiles}")
                    return smiles
                else:
                    print(f"⚠️  Could not resolve '{name}' via name resolution service")
            except Exception as e:
                print(f"⚠️  Error resolving '{name}': {e}")
        
        molecule_names = {
            'aspirin': 'CC(=O)OC1=CC=CC=C1C(=O)O',
            'caffeine': 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C',
            'ibuprofen': 'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O',
            'paracetamol': 'CC(=O)NC1=CC=C(C=C1)O',
            'acetaminophen': 'CC(=O)NC1=CC=C(C=C1)O',
            'ciprofloxacin': 'C1CC1N2C=C(C(=O)C3=CC(=C(C=C32)N4CCNCC4)F)C(=O)O'
        }
        
        resolved = molecule_names.get(name.lower())
        if resolved:
            print(f"✓ Resolved '{name}' from hardcoded dictionary: {resolved}")
        else:
            print(f"⚠️  Could not resolve molecule name: {name}")
        
        return resolved

    def _normalize_protein_list(self, proteins: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Normalize protein entries ensuring valid sequences/IDs and unique chain IDs."""

        if not proteins:
            return []

        normalized: List[Dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()

        for protein in proteins:
            if not isinstance(protein, dict):
                continue

            chain_id_raw = protein.get('chain_id')
            if isinstance(chain_id_raw, list):
                chain_id_raw = next((str(cid).strip() for cid in chain_id_raw if cid), None)
            chain_id = str(chain_id_raw).strip() if chain_id_raw else None

            sequence = protein.get('sequence')
            if sequence:
                sequence = ''.join(str(sequence).split()).upper()
                if len(sequence) < 10 or not set(sequence) <= self.AMINO_ACIDS:
                    sequence = None

            uniprot_id = protein.get('uniprot_id')
            if uniprot_id:
                uniprot_id = str(uniprot_id).strip().upper()
                if not re.fullmatch(r'[A-Z0-9\-]{5,12}', uniprot_id):
                    uniprot_id = None

            if not sequence and not uniprot_id:
                continue

            key = ('SEQ', sequence) if sequence else ('UNIPROT', uniprot_id or '')
            if key in seen_keys:
                continue
            seen_keys.add(key)

            entry: Dict[str, Any] = {
                'chain_id': chain_id,
                'sequence': sequence,
                'uniprot_id': uniprot_id,
            }

            for optional_key in ('msa', 'cyclic', 'modifications'):
                if optional_key in protein:
                    entry[optional_key] = protein[optional_key]

            normalized.append(entry)

        used_ids: set[str] = set()
        for idx, entry in enumerate(normalized):
            cid = entry.get('chain_id')
            if not cid:
                cid = chr(ord('A') + idx)
            cid = str(cid)
            base_cid = cid
            suffix = 1
            while cid in used_ids:
                cid = f"{base_cid}{suffix}"
                suffix += 1
            entry['chain_id'] = cid
            used_ids.add(cid)

        return normalized


def _select_argument_extractor_spec(state: WorkflowState, registry: ToolRegistry) -> ToolSpec:
    stage_config = state.stage_config.get("extract_arguments", {}) if isinstance(state.stage_config, dict) else {}
    explicit_tool_id = (
        stage_config.get("tool_id")
        or state.parsed_arguments.get("argument_extractor_tool")
        or state.parsed_arguments.get("argument_extractor")
    )
    if explicit_tool_id:
        spec = registry.get(str(explicit_tool_id))
        if spec.kind != ToolKind.ARGUMENT_EXTRACTOR:
            raise NodeError(
                f"Configured tool {spec.id!r} is not an argument extractor",
                node="extract_arguments",
                code="ARGUMENT_EXTRACTOR_BAD_KIND",
                details={"tool_id": spec.id, "kind": spec.kind.value},
            )
        return spec

    matches = registry.select(
        kind=ToolKind.ARGUMENT_EXTRACTOR,
        provides=["parsed_arguments"],
        context=state,
    )
    if matches:
        return matches[0]

    raise NodeError(
        "No argument extractor tool is registered",
        node="extract_arguments",
        code="ARGUMENT_EXTRACTOR_UNAVAILABLE",
    )


def _create_argument_extractor_tool(
    registry: ToolRegistry,
    spec: ToolSpec,
    state: WorkflowState,
) -> Any:
    if spec.id == "hybrid_argument_extractor":
        return registry.create(spec.id, llm_client=getattr(state, "llm_client", None))
    return registry.create(spec.id)


def _run_argument_extractor_tool(
    spec: ToolSpec,
    tool: Any,
    request: ArgumentExtractionRequest,
) -> ArgumentExtractionResult:
    if hasattr(tool, "extract"):
        return _normalize_argument_extraction_result(tool.extract(request), spec.id)

    if callable(tool):
        raise TypeError(
            f"Argument extractor {spec.id} is a node callable, not a reusable extractor implementation"
        )

    raise TypeError(f"Argument extractor {spec.id} does not expose extract(request)")


def _normalize_argument_extraction_result(raw_result: Any, tool_id: str) -> ArgumentExtractionResult:
    if isinstance(raw_result, ArgumentExtractionResult):
        raw_result.metadata.setdefault("tool_id", tool_id)
        return raw_result

    if isinstance(raw_result, dict):
        result = _argument_result_from_dict(raw_result, default_method=tool_id)
        result.metadata.setdefault("tool_id", tool_id)
        return result

    raise TypeError(f"Unexpected argument extraction result type: {type(raw_result)}")


def extract_arguments_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Extract arguments using a registered argument extractor.
    """
    state.log("extract_arguments_started")

    registry = get_tool_registry()
    extractor_spec = _select_argument_extractor_spec(state, registry)
    state.record_tool_selection(
        registry.selection_for(
            stage=ToolKind.ARGUMENT_EXTRACTOR,
            spec=extractor_spec,
            reason="Selected by argument extraction capability and configured preference.",
        )
    )

    request = ArgumentExtractionRequest(
        prompt=state.user_prompt,
        context={
            "workflow_id": state.workflow_id,
            "stage_config": state.stage_config.get("extract_arguments", {}) if isinstance(state.stage_config, dict) else {},
        },
    )

    try:
        extractor = _create_argument_extractor_tool(registry, extractor_spec, state)
        extraction_result = _run_argument_extractor_tool(extractor_spec, extractor, request)
        final_result = dict(extraction_result.parsed_arguments)
        if not final_result:
            final_result = {
                "starting_molecules": extraction_result.starting_molecules,
                "target_properties": extraction_result.target_properties,
                "proteins": extraction_result.proteins,
                "confidence_score": extraction_result.confidence_score,
                "extraction_method": extraction_result.method,
            }
        state.record_tool_run(ToolRunRecord(
            tool_id=extractor_spec.id,
            stage=ToolKind.ARGUMENT_EXTRACTOR,
            status="completed",
            inputs=request.model_dump(mode="json"),
            outputs=extraction_result.model_dump(mode="json"),
        ))
    except Exception as exc:
        state.record_tool_run(ToolRunRecord(
            tool_id=extractor_spec.id,
            stage=ToolKind.ARGUMENT_EXTRACTOR,
            status="failed",
            inputs=request.model_dump(mode="json"),
            errors=[str(exc)],
        ))
        raise

    print(f"\n🛠️  Final Extracted Arguments: {final_result}")
    
    state.parsed_arguments = final_result
    state.starting_molecules = final_result.get('starting_molecules', [])

    # Normalize and persist protein targets
    raw_proteins = final_result.get('proteins', []) or []
    validated_proteins: List[ProteinTarget] = []
    serialized_proteins: List[Dict[str, Any]] = []

    for idx, protein in enumerate(raw_proteins):
        try:
            protein_target = ProteinTarget(**protein)
            validated_proteins.append(protein_target)
            serialized_proteins.append(protein_target.model_dump(exclude_none=True))
        except ValidationError as exc:
            print(f"⚠️  Skipping protein target {idx} due to validation error: {exc}")

    state.protein_targets = validated_proteins
    final_result['proteins'] = serialized_proteins
    
    # Convert target properties to TargetProperty objects
    target_dicts = final_result.get('target_properties', []) or []

    if state.protein_targets:
        has_affinity = any(
            tp.get('property_name', '').lower() == 'binding_affinity'
            for tp in target_dicts
        )
        if not has_affinity:
            target_dicts.append({
                'property_name': 'binding_affinity',
                'optimization_mode': OptimizationMode.MAXIMIZE.value,
                'bounds': get_property_catalog().bounds_for('binding_affinity'),
                'transformation': 'LINEAR',
                'weight': 0.0,
                'source': 'auto_boltz'
            })
            final_result['target_properties'] = target_dicts
            final_result.setdefault('auto_added_targets', []).append('binding_affinity')

    if target_dicts:
        equal_weight = 1.0 / len(target_dicts)
        for target in target_dicts:
            target['weight'] = equal_weight

        state.targets = [
            TargetProperty(
                name=tp['property_name'],
                mode=OptimizationMode(tp['optimization_mode']),
                weight=tp['weight'],
                bounds=tuple(tp['bounds']) if tp.get('bounds') else None,
                transformation=tp.get('transformation')
            ) for tp in target_dicts
        ]
    else:
        state.targets = []

    final_result['target_properties'] = target_dicts

    if final_result.get('molecule_source'):
        state.molecule_source = MoleculeSource(final_result['molecule_source'])
    
    if final_result.get('max_iterations'):
        state.max_iterations = final_result['max_iterations']
    
    method = final_result.get('extraction_method', 'unknown')
    confidence = final_result.get('confidence_score', 0)
    
    confidence_details = {
        'overall_confidence': confidence,
        'method': method,
        'proteins_detected': len(state.protein_targets)
    }
    
    if method == 'llm':
        confidence_details['llm_self_confidence'] = final_result.get('llm_self_confidence')
        confidence_details['extraction_quality'] = final_result.get('extraction_quality')
        if final_result.get('rule_supplements'):
            confidence_details['rule_supplements'] = final_result.get('rule_supplements')
    elif method == 'rule_based':
        confidence_details['rule_confidence'] = final_result.get('rule_confidence')
        if final_result.get('fallback_reason'):
            confidence_details['reason'] = final_result.get('fallback_reason')
    
    print(f"\n📊 Extraction Summary:")
    print(f"   Method: {method.upper()}")
    print(f"   Overall Confidence: {confidence:.2f}")
    if method == 'llm':
        llm_self_conf = confidence_details.get('llm_self_confidence')
        print(f"   LLM Self-Confidence: {llm_self_conf if llm_self_conf is not None else 'N/A'}")
        extraction_quality = confidence_details.get('extraction_quality')
        if extraction_quality is not None:
            print(f"   Extraction Quality: {extraction_quality:.2f}")
        else:
            print("   Extraction Quality: N/A")
    elif method == 'rule_based':
        rule_confidence = confidence_details.get('rule_confidence')
        if rule_confidence is not None:
            print(f"   Rule Confidence: {rule_confidence:.2f}")
        else:
            print("   Rule Confidence: N/A")
    print(f"   Proteins Detected: {confidence_details.get('proteins_detected', 0)}")
    
    state.log("extract_arguments_completed", {
        **final_result,
        'confidence_details': confidence_details
    })
    
    return state
