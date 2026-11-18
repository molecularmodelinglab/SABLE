---
name: openfe
description: Create owned OpenFE campaigns from uploaded coordinate artifacts, explicitly sequence preparation and execution, and collect absolute or reference-relative free energies.
---

Use the configured `openfe` origin and optional service credential reference.
Never supply arbitrary provider campaign names. `create` derives the name from
the owned campaign/action and resolves `protein_artifact_id` (PDB) and each ligand's
`artifact_id` (SDF) through `context.read_artifact`. Request uploaded artifacts;
never ask the model to reconstruct coordinates. Provide unique ligand IDs, correct
SMILES, protocol and repeat count. Network protocols additionally require a
reference ligand ID from that same list.

Coordinates must already be in one bound coordinate frame. Each SDF represents
one ligand, and the PDB represents the intended protein without unresolved extra
components. Before queueing, RDKit requires exactly one readable molecule, finite
3D coordinates, and canonical graph/ stereochemistry agreement with the declared
SMILES after removing explicit hydrogens. PDB input must contain ATOM or HETATM
records; full protein format validation is delegated to the service. These checks
cannot establish that a pose is correctly bound or aligned. The package does not
generate poses, choose protonation states, remove
cofactors, or resolve ambiguous multi-ligand files. Upload is limited to 32 ligands
and 3 MB total coordinate text. These are a narrow subset of the hosted API:
advanced settings, cofactor specifications, relaxation controls, selectors, custom
networks and execution-profile selection are unsupported. The service chooses its
default execution profile. Network input preserves supplied poses (`keep`) with a
radial reference network; ABFE disables ligand neutralization.

After create finishes, call `advance` with the previous completed action ID and
`stage: prep`, then `plan`, then `submit`. Each is a new durable job with one POST;
its poll observes only GET campaign status. Phase completion means that phase
finished, not that free energies are ready (Slurm submission may only queue work).
Do not branch or run concurrent phases against one campaign.

Then call `collect` with the completed submit action ID. Submission validates the
owned campaign and returns its ID without gathering. Automatic polling gathers
through GET `/results` or `/network` until all runs finish; incomplete runs remain
`running`, with no scientific result and no new model action required. The reference
API's `gather_campaign` deterministically writes result tables and marks completed
runs done without launching computation. This is an explicit, repeat-safe result
materialization exception to read-only polling. No poll starts prep, plan, or
simulation submission. Create, stage and collection jobs can be reconciled to their
derived campaign IDs. No provider
cancellation API exists. A local wait deadline does not stop submitted computation.

ABFE values are absolute binding free energies in kcal/mol. RBFE and SepTop values
are fitted network means rebased to the explicit reference, also kcal/mol; neither
is absolute binding free energy. Their reported uncertainty belongs to the network
mean fit, **not** the reference difference (covariance is unavailable). Preserve
protocol, edge direction, reference, native fit values, uncertainty and exclusions.
Reject incomplete repeats, invalid numbers, duplicate identities and failed edges
that contributed partial estimates to a fit. Missing/disconnected ligands produce
partial results only when a valid reference and candidate remain.

Failed quality checks are rejected with structured diagnostics by default. An
explicit `allow_failed_quality: true` permits flagged values and retains warnings;
it never bypasses execution/repeat/numeric/identity validation.

Request bounds: create one POST; advance one status GET plus one POST; stage/create
poll one status GET; collect submit one status GET; collect poll at most three GETs.
No HTTP retries or redirects.
Each request has 20-second I/O timeout, 30-second elapsed response check and 4 MB
decoded-response limit. The handlers adapt scientific checks from
`tools/openfe_tool.py` without invoking its blocking wait wrappers.
