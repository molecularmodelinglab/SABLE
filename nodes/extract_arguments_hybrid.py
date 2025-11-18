"""
Extract and parse arguments from user prompt using hybrid LLM + rule-based approach.
"""

import re
import json
from typing import Dict, Any, List, Optional

from pydantic import ValidationError

from schemas.state import (
    WorkflowState,
    TargetProperty,
    OptimizationMode,
    MoleculeSource,
    ProteinTarget,
)
try:
    from rdkit import Chem  # type: ignore
    _RDKit_AVAILABLE = True
except Exception:
    Chem = None  # type: ignore
    _RDKit_AVAILABLE = False

def _is_likely_smiles(s: str) -> bool:
    """Conservative SMILES validator."""
    if not isinstance(s, str) or not s or len(s) > 200 or ' ' in s:
        return False

    # Quick reject: pure letters that look like normal words
    if re.fullmatch(r"[A-Za-z]+", s):
        return False

    # Require at least one structure character or ring/special token
    if not re.search(r"[=#@()\[\]\.0-9]", s) and not re.search(r"(Cl|Br)", s):
        return False

    if _RDKit_AVAILABLE:
        try:
            mol = Chem.MolFromSmiles(s, sanitize=False)
            if mol is None:
                return False
            try:
                Chem.SanitizeMol(mol, catchErrors=True)
            except Exception:
                return False
            return True
        except Exception:
            return False
    else:
        return bool(re.search(r"^(?:\[.*?\]|Br|Cl|[A-Z][a-z]?|[cnops])+[A-Za-z0-9@+\-\[\]()=#%\.]*$", s))

class HybridArgumentExtractor:
    """Combines LLM-based extraction with rule-based validation and fallbacks."""
    # Property bounds based on known ranges - shared across methods
    PROPERTY_BOUNDS = {
        'qed': (0.0, 1.0),
        'logp': (-10.0, 10.0),
        'tpsa': (0.0, 300.0),
        'molecular_weight': (0.0, 1000.0),
        'h_bond_donors': (0.0, 20.0),
        'h_bond_acceptors': (0.0, 20.0),
        'rotatable_bonds': (0.0, 30.0),
        'ring_count': (0.0, 10.0),
        'heavy_atom_count': (0.0, 100.0),
        'solubility': (-10.0, 0.0),  # logS scale
        'fsp3': (0.0, 1.0),
        'cns_activity': (0.0, 1.0),
        'toxicity': (0.0, 1.0),
        'binding_affinity': (-20, 20.0),
        'permeability': (0.0, 1000.0)  # nm/s
    }

    AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWYBXZJUO")
    
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        
    def extract_with_llm(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Extract arguments using LLM with structured output."""
        if not self.llm_client:
            return None
            
        extraction_prompt = f"""
You are a chemistry AI assistant. Extract structured information from the user's request about molecular optimization.

User Request: "{prompt}"

Extract the following information and respond with a JSON object:
- starting_molecules: List of SMILES strings or molecule names mentioned
- target_properties: List of objects with property_name, optimization_mode (MAX/MIN/MATCH), weight, bounds (tuple)
- proteins: List of objects describing protein chains with chain_id (A, B, ...), and either sequence or uniprot_id (optional fields: msa, cyclic, modifications)
- molecule_source: How to obtain molecules (generated/provided/enumerated/external_library)
- max_iterations: Number of optimization rounds (default 10, max 100)
- batch_size: Molecules per iteration (default 5, max 50)
- enumeration_size: Size of enumerated library (default 100, max 1000)
- llm_confidence: Your confidence in this extraction (0.0-1.0)

Available properties: qed, logp, tpsa, molecular_weight, h_bond_donors, h_bond_acceptors, 
rotatable_bonds, ring_count, heavy_atom_count, solubility, fsp3, cns_activity, toxicity, 
binding_affinity, permeability

Note for binding_affinity: This is expressed in Log10 Kd (nM), where lower is better, so we ideally want to minimize.

LLM Confidence score guidelines:
- 0.9-1.0: All required fields clearly specified, no ambiguity
- 0.7-0.9: Most fields clear, minor assumptions needed
- 0.5-0.7: Some fields missing or ambiguous, moderate assumptions
- 0.3-0.5: High ambiguity, many assumptions required
- 0.0-0.3: Very unclear request, mostly guessing

Respond with valid JSON only.
"""
        
        try:
            response = self.llm_client.generate(extraction_prompt)
            # Clean response to extract JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                extracted_data = json.loads(json_match.group())
                
                # Get LLM's self-reported confidence or calculate it
                llm_self_confidence = extracted_data.get('llm_confidence', None)
                if llm_self_confidence is not None:
                    llm_self_confidence = float(llm_self_confidence)
                
                # Calculate our own assessment of the extraction quality
                quality_confidence = self._assess_llm_extraction_quality(extracted_data, prompt)
                
                # Store both for transparency
                extracted_data['llm_self_confidence'] = llm_self_confidence if llm_self_confidence is not None else quality_confidence
                extracted_data['extraction_quality'] = quality_confidence
                extracted_data['extraction_method'] = 'llm'
                
                # Final LLM confidence is the minimum of both (conservative)
                if llm_self_confidence is not None:
                    extracted_data['confidence_score'] = min(llm_self_confidence, quality_confidence)
                else:
                    extracted_data['confidence_score'] = quality_confidence
                
                return extracted_data
        except Exception as e:
            print(f"LLM extraction failed: {e}")
            return None
    
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
        """Existing regex-based extraction logic from extract_arguments.py."""
        prompt_lower = prompt.lower()
        parsed = {}
        
        # Extract starting molecule (look for SMILES patterns or molecule names)
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
        
        # Common molecule names
        molecule_names = {
            'aspirin': 'CC(=O)OC1=CC=CC=C1C(=O)O',
            'caffeine': 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C',
            'ibuprofen': 'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O',
            'paracetamol': 'CC(=O)NC1=CC=C(C=C1)O',
            'acetaminophen': 'CC(=O)NC1=CC=C(C=C1)O'
        }
        
        for name, smiles in molecule_names.items():
            if name in prompt_lower:
                parsed.setdefault('starting_molecules', [])
                if smiles not in parsed['starting_molecules']:
                    parsed['starting_molecules'].append(smiles)

        # Extract protein targets (sequences or UniProt IDs)
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
        property_keywords = {
            'qed': ['qed', 'drug-likeness', 'drug likeness', 'druglike'],
            'logp': ['logp', 'lipophilicity', 'hydrophobicity', 'partition'],
            'tpsa': ['tpsa', 'polar surface area', 'psa'],
            'molecular_weight': ['molecular weight', 'mw', 'weight', 'mass'],
            'h_bond_donors': ['h bond donor', 'hbd', 'hydrogen bond donor', 'donor'],
            'h_bond_acceptors': ['h bond acceptor', 'hba', 'hydrogen bond acceptor', 'acceptor'],
            'rotatable_bonds': ['rotatable', 'flexibility', 'rotatable bond'],
            'ring_count': ['ring', 'cyclic', 'rings'],
            'heavy_atom_count': ['heavy atom', 'non-hydrogen', 'heavy atoms'],
            'solubility': ['solubility', 'soluble', 'water solubility', 'aqueous'],
            'fsp3': ['fsp3', 'fraction sp3', 'saturation'],
            'cns_activity': ['cns', 'brain', 'bbb', 'blood brain barrier', 'central nervous'],
            'toxicity': ['toxicity', 'toxic', 'safe', 'safety'],
            'binding_affinity': ['binding', 'affinity', 'ic50', 'ki', 'kd'],
            'permeability': ['permeability', 'permeable', 'caco-2', 'caco2']
        }
        
        targets = []
        for prop, keywords in property_keywords.items():
            if any(kw in prompt_lower for kw in keywords):
                mode = OptimizationMode.MAXIMIZE
                if prop == 'toxicity' and any(word in prompt_lower for word in ['reduce', 'minimize', 'lower', 'decrease']):
                    mode = OptimizationMode.MINIMIZE
                elif prop == 'molecular_weight' and any(word in prompt_lower for word in ['reduce', 'minimize', 'lower', 'small']):
                    mode = OptimizationMode.MINIMIZE

                if mode == OptimizationMode.MATCH:
                    transformation = "TRIANGULAR"
                elif mode in [OptimizationMode.MAXIMIZE, OptimizationMode.MINIMIZE]:
                    transformation = "LINEAR"
                
                
                targets.append({
                    'property_name': prop,
                    'optimization_mode': mode.value,
                    'bounds': self.PROPERTY_BOUNDS.get(prop),
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
        
        # Store metadata about extraction
        parsed['extraction_method'] = 'rule_based'
        parsed['rule_confidence'] = min(0.7, rule_confidence)  # Cap at 0.7 for rule-based
        parsed['confidence_score'] = parsed['rule_confidence']  # Overall confidence equals rule confidence
        
        return parsed
    
    def validate_and_merge(self, llm_result: Optional[Dict[str, Any]], 
                          rule_result: Dict[str, Any], 
                          original_prompt: str) -> Dict[str, Any]:
        """
        Validate LLM results and merge with rule-based fallbacks.
        Clearly tracks which extraction method was used and why.
        """
        
        # Decision: Use rule-based if LLM failed or has very low confidence
        if not llm_result or llm_result.get('confidence_score', 0) < 0.3:
            reason = "LLM unavailable" if not llm_result else f"low LLM confidence ({llm_result.get('confidence_score', 0):.2f})"
            print(f"✓ Using rule-based extraction ({reason})")
            rule_result['fallback_reason'] = reason
            return rule_result
        
        # Start with LLM result as base
        print(f"✓ Using LLM extraction (confidence: {llm_result.get('confidence_score', 0):.2f})")
        merged = llm_result.copy()
        merged['used_method'] = 'llm'
        
        # Track any supplements from rule-based
        supplements = []
        
        # Validate and supplement SMILES
        validated_smiles = []
        for smiles in merged.get('starting_molecules', []):
            if _is_likely_smiles(smiles):
                validated_smiles.append(smiles)
            else:
                # Try to resolve molecule names
                resolved = self._resolve_molecule_name(smiles)
                if resolved:
                    validated_smiles.append(resolved)
        
        # Add any SMILES found by rules that LLM missed
        rule_smiles_added = 0
        for smiles in rule_result.get('starting_molecules', []):
            if smiles not in validated_smiles:
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
                prop_name = prop.get('property_name', '').lower()
                
                # Add bounds if completely missing
                if not prop.get('bounds') and prop_name in self.PROPERTY_BOUNDS:
                    prop['bounds'] = self.PROPERTY_BOUNDS[prop_name]
                    bounds_added += 1
                elif prop.get('bounds'):
                    # Fix partial bounds (e.g., [None, 60] or [2, None])
                    bounds = prop['bounds']
                    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                        lower, upper = bounds
                        default_bounds = self.PROPERTY_BOUNDS.get(prop_name)
                        
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
                
                # Add transformation if missing
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
        """Resolve common molecule names to SMILES."""
        molecule_names = {
            'aspirin': 'CC(=O)OC1=CC=CC=C1C(=O)O',
            'caffeine': 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C',
            'ibuprofen': 'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O',
            'paracetamol': 'CC(=O)NC1=CC=C(C=C1)O',
            'acetaminophen': 'CC(=O)NC1=CC=C(C=C1)O'
        }
        return molecule_names.get(name.lower())

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

def extract_arguments_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Extract arguments using hybrid LLM + rule-based approach.
    """
    state.log("extract_arguments_started")
    
    # Initialize extractor
    extractor = HybridArgumentExtractor(llm_client=getattr(state, 'llm_client', None))
    
    # Try LLM extraction first
    llm_result = extractor.extract_with_llm(state.user_prompt)
    
    # Always run rule-based as fallback/validation
    rule_result = extractor.extract_with_rules(state.user_prompt)
    
    # Validate and merge results
    final_result = extractor.validate_and_merge(llm_result, rule_result, state.user_prompt)

    print(f"\n🛠️  Final Extracted Arguments: {final_result}")
    
    # Update state with extracted arguments
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
                'bounds': extractor.PROPERTY_BOUNDS.get('binding_affinity'),
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

    # Set molecule source
    if final_result.get('molecule_source'):
        state.molecule_source = MoleculeSource(final_result['molecule_source'])
    
    # Set iteration limit
    if final_result.get('max_iterations'):
        state.max_iterations = final_result['max_iterations']
    
    # Build detailed confidence report
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