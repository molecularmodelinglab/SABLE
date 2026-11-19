---
name: rdkit-descriptors
description: Calculate molecular descriptors and QED from explicit SMILES using RDKit.
---

Use `calculate` for physicochemical, hydrogen-bond, topology, and complexity descriptors, and QED. Request properties using the keys below. Supply explicit SMILES; ask the user for structures when a name cannot be resolved from available evidence. Do not guess a structure from a drug name.

Each result retains the input SMILES, canonical isomeric SMILES, RDKit version, property units, and computational method. Invalid structures appear in `failures`, not as zero-valued observations. Mixtures with disconnected components are rejected.

These are calculated descriptors. QED is a drug-likeness heuristic; LogP is a calculated partition coefficient. Neither measures solubility, permeability, safety, potency, or biological activity. Clarify or explain unsupported objectives instead of treating a descriptor as a substitute. Do not compare observations from incompatible methods or conditions without identifying that difference.

For a generated library or BayBE batch, pass its `source_action_id` instead of SMILES. Optionally select `candidate_ids` from that source; otherwise all candidates in the source are evaluated (maximum 32). This preserves candidate identity and library provenance for downstream optimization. Do not retype generated SMILES or measurement values into an optimizer call.

## Descriptor reference

Each function below is from `rdkit.Chem.Descriptors`. Property keys are stable API names; use them in `properties`, rather than the RDKit function names. Existing keys remain supported.

| Property key | RDKit function | Description | Output unit | Drug-like reference range |
| --- | --- | --- | --- | --- |
| `molecular_weight` | `MolWt` | Average molecular weight | g/mol | 150–500 |
| `exact_molecular_weight` | `ExactMolWt` | Monoisotopic mass | Da | 150–500 |
| `heavy_atom_molecular_weight` | `HeavyAtomMolWt` | Molecular weight excluding hydrogens | g/mol | — |
| `logp` | `MolLogP` | Wildman–Crippen calculated LogP | dimensionless | −0.4–5.6 |
| `molar_refractivity` | `MolMR` | Wildman–Crippen molar refractivity | cm^3/mol | 40–130 |
| `tpsa` | `TPSA` | Topological polar surface area | angstrom^2 | 20–140 |
| `labute_asa` | `LabuteASA` | Labute approximate surface area | angstrom^2 | — |
| `h_bond_donors` | `NumHDonors` | Lipinski hydrogen-bond donors | count | 0–5 |
| `h_bond_acceptors` | `NumHAcceptors` | Lipinski hydrogen-bond acceptors | count | 0–10 |
| `no_count` | `NOCount` | Number of nitrogen and oxygen atoms | count | — |
| `nhoh_count` | `NHOHCount` | Number of NH and OH groups | count | — |
| `rotatable_bonds` | `NumRotatableBonds` | Rotatable bonds using RDKit’s default definition | count | — |
| `ring_count` | `RingCount` | Total rings using RDKit’s ring perception | count | — |
| `aromatic_rings` | `NumAromaticRings` | Aromatic rings | count | — |
| `aliphatic_rings` | `NumAliphaticRings` | Non-aromatic rings | count | — |
| `saturated_rings` | `NumSaturatedRings` | Fully saturated rings | count | — |
| `heteroatoms` | `NumHeteroatoms` | Atoms other than carbon or hydrogen | count | — |
| `heterocycles` | `NumHeterocycles` | Rings containing heteroatoms | count | — |
| `fraction_csp3` | `FractionCSP3` | Fraction of carbon atoms that are sp3-hybridized (0–1) | dimensionless | — |
| `heavy_atom_count` | `HeavyAtomCount` | Non-hydrogen atoms | count | — |
| `valence_electrons` | `NumValenceElectrons` | Total valence electrons | count | — |
| `radical_electrons` | `NumRadicalElectrons` | Radical electrons | count | — |
| `qed` | `rdkit.Chem.QED.qed` | Quantitative estimate of drug-likeness (0–1) | dimensionless | — |

The reference ranges are optional pharmaceutical screening heuristics, not pass/fail rules or evidence of efficacy or safety. Values outside these ranges are still returned. Do not apply them automatically to solvents, materials, or other domains. Average molecular weight is reported in g/mol; its numerical value corresponds to average molecular mass in Da. Exact molecular mass is reported in Da. Ring counts use RDKit ring perception (symmetrized SSSR), which can differ from a minimal SSSR for some polycyclic structures. Aliphatic rings need not be saturated. NOCount and NHOHCount use RDKit's definitions and must not be substituted for Lipinski donor/acceptor counts.

Example: `{"smiles": ["CCO"], "properties": ["exact_molecular_weight", "molar_refractivity", "labute_asa", "fraction_csp3", "radical_electrons"]}`.
