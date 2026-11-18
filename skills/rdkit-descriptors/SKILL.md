---
name: rdkit-descriptors
description: Calculate molecular descriptors and QED from explicit SMILES using RDKit.
---

Use `calculate` for molecular weight, LogP, TPSA, hydrogen-bond counts, rotatable bonds, and QED. Supply explicit SMILES; ask the user for structures when a name cannot be resolved from available evidence. Do not guess a structure from a drug name.

Each result retains the input SMILES, canonical isomeric SMILES, RDKit version, property units, and computational method. Invalid structures appear in `failures`, not as zero-valued observations. Mixtures with disconnected components are rejected in this first version.

These are calculated descriptors. QED is a drug-likeness heuristic; LogP is a calculated partition coefficient. Neither measures solubility, permeability, safety, potency, or biological activity. Clarify or explain unsupported objectives instead of treating a descriptor as a substitute. Do not compare observations from incompatible methods or conditions without identifying that difference.
