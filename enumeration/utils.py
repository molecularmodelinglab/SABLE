'''
    This file contains helper functions for the project.
'''

import os
import json
import base64
import jax
import numpy as np
import jax.numpy as jnp

from rdkit import RDLogger, Chem
from rdkit.Chem import AllChem, DataStructs, Draw, rdFMCS
from .reaction import ReactionTemplate21

RDLogger.DisableLog('rdApp.*')

rdColors = {
    'blue': (0.19, 0.51, 0.70),
    'purple': (0.68, 0.45, 0.8),
    'pink': (0.94, 0.32, 0.65),
    'green': (0.48, 0.68, 0.35),
    'yellow': (0.81, 0.82, 0.0),
    'red': (0.95, 0.42, 0.19),
    'orange': (0.93, 0.69, 0.17)
}


def load_reactions_from_json(file_path) -> list[ReactionTemplate21]:
    '''
        Loads reactions from a json file.

        Args:
            file_path: str, path to the json file.

        Returns:
            list of ReactionTemplate21 objects.

        link to reaction data source:
            https://github.com/datamol-io/datamol/blob/9e94d026534b2a534250dfbfab924ab6f089e477/datamol/data/reactions.json
    '''
    with open(file_path, 'r') as file:
        data = json.load(file)

    reactions = []
    for key, values in data.items():
        reaction = ReactionTemplate21.from_reaction_json(name=key, reaction_json=values)
        reactions.append(reaction)

    return reactions

@jax.jit
def get_batch_tani_sims_jax(query_fps, stock_fps):
    """
        Calculates the Tanimoto similarity between query fingerprints and
        stock fingerprints in batches using jax.

        Args:
            query_fps: jnp.ndarray, query fingerprints, shape=(n_query, 2048).
            stock_fps: jnp.ndarray, stock fingerprints, shape=(n_stock, 2048).

        Returns:
            jnp.ndarray, Tanimoto similarities, shape=(n_query, n_stock).
    """
    intersection = jnp.matmul(query_fps, jnp.transpose(stock_fps))
    union = (jnp.sum(query_fps, axis=-1, keepdims=True) +
             jnp.sum(stock_fps, axis=-1, keepdims=False) -
             intersection)
    return intersection / union

def get_batch_tani_sims_rdkit(query_fps, stock_fps):
    '''
        Calculates the Tanimoto similarity between query fingerprints and
        stock fingerprints in batches.

        NOTE: this function can be faster than `get_batch_tani_sims_tf` if
            running on a single CPU core.

        Args:
            query_fps: list of rdkit DataStructs.ExplicitBitVect, query fingerprints.
            stock_fps: list of rdkit DataStructs.ExplicitBitVect, stock fingerprints.

        Returns:
            np.ndarray, Tanimoto similarities, shape=(n_query, n_stock).
    '''
    sims = np.zeros((len(query_fps), len(stock_fps)))
    for i, query_fp in enumerate(query_fps):
        sims[i] = DataStructs.BulkTanimotoSimilarity(query_fp, stock_fps)
    return sims

def get_tani_sim_fp(fp1, fp2):
    '''
        Calculates the Tanimoto similarity between two fingerprints.

        Args:
            fp1: rdkit.Chem.rdchem.Mol, molecule 1.
            fp2: rdkit.Chem.rdchem.Mol, molecule 2.

        Returns:
            float, Tanimoto similarity.
    '''
    return DataStructs.TanimotoSimilarity(fp1, fp2)

def get_svg_mol(mol, sub_mol=None, sub_mol_color='green', legend='', show_idx=False, return_drawing=False):
    '''
        Get svg image of a molecule with a substructure highlighted.
    '''
    sub_mol_color = rdColors[sub_mol_color]
    if isinstance(mol, str):
        mol = Chem.MolFromSmiles(mol)
        if mol is None:
            raise ValueError('Invalid molecule')
    AllChem.Compute2DCoords(mol)
    if sub_mol is not None:
        if isinstance(sub_mol, str):
            sub_struct = Chem.MolFromSmiles(sub_mol)
        else:
            sub_struct = sub_mol
        assert sub_struct is not None, 'Invalid substructure'
        assert mol.HasSubstructMatch(sub_struct), 'Substructure not found'
        hit_atoms = list(mol.GetSubstructMatch(sub_struct))
        hit_bonds = []
        for bond in sub_struct.GetBonds():
            a1 = hit_atoms[bond.GetBeginAtomIdx()]
            a2 = hit_atoms[bond.GetEndAtomIdx()]
            hit_bonds.append(mol.GetBondBetweenAtoms(a1, a2).GetIdx())
    else:
        hit_atoms = []
        hit_bonds = []
    if show_idx:
        for i, atom in enumerate(mol.GetAtoms()):
            atom.SetProp('atomNote', str(i))
    drawing = Draw.MolDraw2DSVG(350, 150)
    drawing.DrawMolecule(mol, highlightAtoms=hit_atoms, highlightBonds=hit_bonds,
                          highlightAtomColors={i: sub_mol_color for i in hit_atoms},
                          highlightBondColors={i: sub_mol_color for i in hit_bonds},
                          legend=legend)
    drawing.FinishDrawing()
    if return_drawing:
        return drawing.GetDrawingText()
    svg = drawing.GetDrawingText()
    svg = base64.b64encode(svg.encode('utf-8')).decode('utf-8')
    return f"data:image/svg+xml;base64,{svg}"

def get_svg_mol_with_bbs(mol, bb1, bb2, bb_colors=['red', 'green'], legend=''):
    '''
        Similar to get_svg_mol, but instead of highlighting a substructure,
        it highlights the building blocks.
    '''
    bb_colors = [rdColors[c] for c in bb_colors]
    if isinstance(mol, str):
       mol = Chem.MolFromSmiles(mol)
    AllChem.Compute2DCoords(mol)
    if isinstance(bb1, str):
        bb1 = Chem.MolFromSmiles(bb1)
    if isinstance(bb2, str):
        bb2 = Chem.MolFromSmiles(bb2)
    assert bb1 is not None, 'Invalid building block 1'
    assert bb2 is not None, 'Invalid building block 2'
    
    mcs1 = rdFMCS.FindMCS([mol, bb1])
    mcs2 = rdFMCS.FindMCS([mol, bb2])
    smarts1 = mcs1.smartsString
    smarts2 = mcs2.smartsString
    bb1 = Chem.MolFromSmarts(smarts1)
    bb2 = Chem.MolFromSmarts(smarts2)
    hit_atoms1 = list(mol.GetSubstructMatch(bb1))
    hit_atoms2 = list(mol.GetSubstructMatch(bb2))
    hit_bonds1 = []
    hit_bonds2 = []
    for bond in bb1.GetBonds():
        a1 = hit_atoms1[bond.GetBeginAtomIdx()]
        a2 = hit_atoms1[bond.GetEndAtomIdx()]
        try:
            hit_bonds1.append(mol.GetBondBetweenAtoms(a1, a2).GetIdx())
        except:
            pass
    for bond in bb2.GetBonds():
        a1 = hit_atoms2[bond.GetBeginAtomIdx()]
        a2 = hit_atoms2[bond.GetEndAtomIdx()]
        try:
            hit_bonds2.append(mol.GetBondBetweenAtoms(a1, a2).GetIdx())
        except:
            pass
    drawing = Draw.MolDraw2DSVG(350, 150)
    drawing.DrawMolecule(mol, highlightAtoms=hit_atoms1+hit_atoms2, highlightBonds=hit_bonds1+hit_bonds2,
                          highlightAtomColors={**{i: bb_colors[0] for i in hit_atoms1}, **{i: bb_colors[1] for i in hit_atoms2}},
                          highlightBondColors={**{i: bb_colors[0] for i in hit_bonds1}, **{i: bb_colors[1] for i in hit_bonds2}},
                          legend=legend)
    drawing.FinishDrawing()
    svg = drawing.GetDrawingText()
    svg = base64.b64encode(svg.encode('utf-8')).decode('utf-8')
    return f"data:image/svg+xml;base64,{svg}"

def _dummy2dummy(mol: Chem.rdchem.Mol):
    '''
        Possibly a useful function, but not used in the project. 
        Helper function to replace [*] with [*H5] in the molecule.

        NOTE: [*] causes problems in the reaction SMARTS since they
            are considered as wildcard atoms.

        Args:
            mol: rdkit mol object.

        Returns:
            mol: rdkit mol object with [*] replaced by [*H5].
    '''
    if '[*]' in Chem.MolToSmiles(mol):
        return Chem.MolFromSmiles(Chem.MolToSmiles(mol).replace('[*]', '[*H5]'))
    elif '*' in Chem.MolToSmiles(mol):
        return Chem.MolFromSmiles(Chem.MolToSmiles(mol).replace('*', '[*H5]'))
    else:
        return ValueError('No dummy atom found.')

