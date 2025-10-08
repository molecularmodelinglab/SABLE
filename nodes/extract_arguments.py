"""
Extract and parse arguments from user prompt.
This node analyzes the user's request and extracts key parameters.
"""

import re
from typing import Dict, Any, List
from schemas.state import WorkflowState, TargetProperty, OptimizationMode, MoleculeSource
try:
    from rdkit import Chem  # type: ignore
    _RDKit_AVAILABLE = True
except Exception:
    Chem = None  # type: ignore
    _RDKit_AVAILABLE = False


def _is_likely_smiles(s: str) -> bool:
    """Conservative SMILES validator.

    Prefer RDKit parsing; fallback to simple heuristics if RDKit unavailable.
    Filters out normal words like 'Optimize'.
    """
    if not isinstance(s, str) or not s or len(s) > 200 or ' ' in s:
        return False

    # Quick reject: pure letters that look like normal words
    if re.fullmatch(r"[A-Za-z]+", s):
        return False

    # Require at least one structure character or ring/special token
    if not re.search(r"[=#@()\[\]\.0-9]", s) and not re.search(r"(Cl|Br)", s):
        # If it lacks any typical SMILES symbols, likely not a SMILES
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
        # Heuristic fallback: must contain atom tokens and structure symbols
        return bool(re.search(r"^(?:\[.*?\]|Br|Cl|[A-Z][a-z]?|[cnops])+[A-Za-z0-9@+\-\[\]()=#%\.]*$", s))


def extract_arguments_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Extract arguments from the user prompt.
    This is a deterministic parsing step, not requiring LLM.
    """
    state.log("extract_arguments_started")
    
    prompt = state.user_prompt.lower()
    parsed = {}
    
    # Extract starting molecule (look for SMILES patterns or molecule names)
    # Start with permissive tokenization, then aggressively validate
    smiles_pattern = r"(?:\[.*?\]|Br|Cl|[A-Z][a-z]?|[cnops])[A-Za-z0-9@+\-\[\]()=#%\.]*"
    smiles_matches = re.findall(smiles_pattern, state.user_prompt)
    candidates: List[str] = [s for s in smiles_matches if _is_likely_smiles(s)]
    # Deduplicate while preserving order
    seen = set()
    potential_smiles = []
    for s in candidates:
        if s not in seen:
            seen.add(s)
            potential_smiles.append(s)
    if potential_smiles:
        parsed['starting_molecules'] = potential_smiles[:3]  # Take up to 3
    
    # Common molecule names
    molecule_names = {
        'aspirin': 'CC(=O)OC1=CC=CC=C1C(=O)O',
        'caffeine': 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C',
        'ibuprofen': 'CC(C)CC1=CC=C(C=C1)C(C)C(=O)O',
        'paracetamol': 'CC(=O)NC1=CC=C(C=C1)O',
        'acetaminophen': 'CC(=O)NC1=CC=C(C=C1)O'
    }
    
    for name, smiles in molecule_names.items():
        if name in prompt:
            parsed.setdefault('starting_molecules', [])
            if smiles not in parsed['starting_molecules']:
                parsed['starting_molecules'].append(smiles)
    
    # Extract target properties - aligned with available characterization tools
    # These properties can be calculated by RDKit (MoleculeCharacterizationTool) or Stoplight
    property_keywords = {
        # RDKit properties
        'qed': ['qed', 'drug-likeness', 'drug likeness', 'druglike'],
        'logp': ['logp', 'lipophilicity', 'hydrophobicity', 'partition'],
        'tpsa': ['tpsa', 'polar surface area', 'psa'],
        'molecular_weight': ['molecular weight', 'mw', 'weight', 'mass'],
        'h_bond_donors': ['h bond donor', 'hbd', 'hydrogen bond donor', 'donor'],
        'h_bond_acceptors': ['h bond acceptor', 'hba', 'hydrogen bond acceptor', 'acceptor'],
        'rotatable_bonds': ['rotatable', 'flexibility', 'rotatable bond'],
        'ring_count': ['ring', 'cyclic', 'rings'],
        'heavy_atom_count': ['heavy atom', 'non-hydrogen', 'heavy atoms'],
        
        # Stoplight properties
        'solubility': ['solubility', 'soluble', 'water solubility', 'aqueous'],
        'fsp3': ['fsp3', 'fraction sp3', 'saturation'],
        'cns_activity': ['cns', 'brain', 'bbb', 'blood brain barrier', 'central nervous'],
        
        # Properties that need special handling or external data
        'toxicity': ['toxicity', 'toxic', 'safe', 'safety'],
        'binding_affinity': ['binding', 'affinity', 'ic50', 'ki', 'kd'],
        'permeability': ['permeability', 'permeable', 'caco-2', 'caco2']
    }
    
    # Property bounds based on known ranges
    property_bounds = {
        'qed': (0.0, 1.0),
        'logp': (-10.0, 10.0),
        'tpsa': (0.0, 200.0),
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
        'binding_affinity': (0.0, 20.0),  # pIC50 scale
        'permeability': (0.0, 1000.0)  # nm/s
    }
    
    targets = []
    for prop, keywords in property_keywords.items():
        if any(kw in prompt for kw in keywords):
            mode = OptimizationMode.MAXIMIZE
            if prop == 'toxicity' and any(word in prompt for word in ['reduce', 'minimize', 'lower', 'decrease']):
                mode = OptimizationMode.MINIMIZE
            elif prop == 'molecular_weight' and any(word in prompt for word in ['reduce', 'minimize', 'lower', 'small']):
                mode = OptimizationMode.MINIMIZE
            
            targets.append({
                'name': prop.upper() if prop in ['qed', 'tpsa'] else prop.capitalize(),
                'mode': mode,
                'weight': 1.0 / max(1, len(targets) + 1),  # Equal weights
                'bounds': property_bounds.get(prop),
                'transformation': 'LINEAR'  # Can be set to 'LINEAR', 'LOG', etc. if needed
            })
    
    parsed['targets'] = targets
    
    # Extract iteration/budget constraints
    iteration_match = re.search(r'(\d+)\s*(?:iterations?|rounds?|cycles?)', prompt)
    if iteration_match:
        parsed['max_iterations'] = min(int(iteration_match.group(1)), 100)
    
    # Extract batch size
    batch_match = re.search(r'batch\s*(?:size)?\s*(?:of)?\s*(\d+)', prompt)
    if batch_match:
        parsed['batch_size'] = min(int(batch_match.group(1)), 20)
    
    # Determine molecule source strategy
    if 'enumerate' in prompt or 'analogs' in prompt or 'derivatives' in prompt:
        parsed['molecule_source'] = MoleculeSource.ENUMERATED
    elif 'library' in prompt or 'database' in prompt or 'screen' in prompt:
        parsed['molecule_source'] = MoleculeSource.EXTERNAL_LIBRARY
    elif parsed.get('starting_molecules'):
        parsed['molecule_source'] = MoleculeSource.PROVIDED
    else:
        parsed['molecule_source'] = MoleculeSource.GENERATED
    
    # Extract enumeration size if mentioned
    enum_match = re.search(r'(\d+)\s*(?:molecules?|compounds?|analogs?|derivatives?)', prompt)
    if enum_match:
        parsed['enumeration_size'] = min(int(enum_match.group(1)), 1000)
    
    state.parsed_arguments = parsed
    
    if 'starting_molecules' in parsed:
        state.starting_molecules = parsed['starting_molecules']

    if 'targets' in parsed:
        state.targets = [TargetProperty(**t) for t in parsed['targets']]
    
    if 'molecule_source' in parsed:
        state.molecule_source = parsed['molecule_source']
    
    if 'max_iterations' in parsed:
        state.max_iterations = parsed['max_iterations']
    
    print(f"Extracted arguments: {parsed}")
    state.log("extract_arguments_completed", parsed)
    
    return state