'''
    This script contains the Enumerator class which is used to enumerate
    a given molecule with Enamine building blocks or any other building block
    source. 
'''

# libraries
import os
import abc
from pathlib import Path
import pandas as pd
import numpy as np

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors, rdFingerprintGenerator
from rdkit.Chem.FastSDMolSupplier import FastSDMolSupplier

from collections import namedtuple
from itertools import product as iter_product, chain
from tqdm import tqdm

from . import utils

try:
    NCPUS = int(os.environ['SLURM_CPUS_PER_TASK'])
except KeyError:
    NCPUS = os.cpu_count()

package_root = Path(__file__).parent.parent
class _BaseEnumerator(abc.ABC):
    '''
        Base Enumerator.
    '''
    def __init__(self, molecule: str | Chem.rdchem.Mol, bb_supplier: str, load_reactions: bool=True):
        '''
            Initialize the BaseEnumerator object.

            Args:
                molecule (str): SMILES string or rdkit mol object.
                bb_supplier (str): "US_stock", "EU_stock", "Global_stock",
                                    or "NoRush_stock". A custom path to a file
                                    containing building blocks can also be provided.
                load_reactions (bool): load the reaction templates.
        '''
        # molecule
        if isinstance(molecule, str):
            self.molecule = Chem.MolFromSmiles(molecule)
        else:
            self.molecule = molecule
        flag = Chem.SanitizeMol(self.molecule, catchErrors=True)
        assert flag == Chem.rdmolops.SanitizeFlags.SANITIZE_NONE, f'Molecule sanitization failed with flags: {flag}'

        # building blocks
        if bb_supplier == 'US_stock':
            self._supplier_path = f'{package_root}/enumeration/buildingblocks/Enamine_US_BB_stock_sanitized.sdf'
        elif bb_supplier == 'EU_stock':
            self._supplier_path = f'{package_root}/enumeration/buildingblocks/Enamine_EU_BB_stock_sanitized.sdf'
        elif bb_supplier == 'Global_stock':
            self._supplier_path = f'{package_root}/enumeration/buildingblocks/Enamine_Global_BB_stock_sanitized.sdf'
        elif bb_supplier == 'NoRush_stock':
            self._supplier_path = f'{package_root}/enumeration/buildingblocks/Enamine_NoRush_BB_stock_sanitized.sdf'
        elif bb_supplier == 'test':
            self._supplier_path = f'{package_root}/enumeration/buildingblocks/test_100_bb.sdf'
        else:
            self._supplier_path = bb_supplier

        if bb_supplier in ['US_stock', 'EU_stock', 'Global_stock', 'NoRush_stock', 'test']:
            self.bb_supplier = FastSDMolSupplier(self._supplier_path, sanitize=True)
        else:
            self.bb_supplier = FastSDMolSupplier(self._supplier_path, sanitize=True)

        # reaction data
        if load_reactions:
            self._reactions = utils.load_reactions_from_json(f'{package_root}/enumeration/reactions/reactions.json')
            self._reactions = [reaction for reaction in self._reactions if reaction.is_valid()]

        # fingerprint generator
        self._fp_generator = rdFingerprintGenerator.GetMorganGenerator(
            radius=3, fpSize=2048, includeChirality=True
        )

    @abc.abstractmethod
    def enumerate(self):
        raise NotImplementedError
    
    @abc.abstractmethod
    def get_results(self):
        raise NotImplementedError

    @abc.abstractmethod
    def save_results(self):
        raise NotImplementedError

    @abc.abstractmethod
    def _process_building_blocks(self):
        raise NotImplementedError

    @abc.abstractmethod
    def _prepare_molecule(self):
        raise NotImplementedError
    
    def _get_fingerprints(self, mols, return_np: bool=True):
        '''
            Generates fingerprints for the given molecules.

            Args:
                mols: list of rdkit.Chem.rdchem.Mol, molecules.
                return_np (bool): return numpy array if True, else list.

            Returns:
                np.ndarray | list: fingerprints.
        '''
        if NCPUS > 4 and return_np:
            print(f'Running on {NCPUS} cores. Jax-JIT is enabled.', flush=True)
            return np.asarray([self._fp_generator.GetFingerprint(mol) for mol in mols])
        else:
            print(f'Running on {NCPUS} cores. Jax-JIT is disabled.', flush=True)
            return [self._fp_generator.GetFingerprint(mol) for mol in mols]
        
    def _get_fingerprint(self, mol):
        '''
            Generates fingerprint for the given molecule.

            Args:
                mol: rdkit.Chem.rdchem.Mol, molecule.

            Returns:
                rdkit.DataStructs.cDataStructs.ExplicitBitVect, fingerprint.
        '''
        return self._fp_generator.GetFingerprint(mol)


class SiteEnumerator(_BaseEnumerator):
    '''
        Site Enumerator class is used to enumerate a given molecule with building blocks.
    '''
    def __init__(
            self, 
            molecule: str | Chem.rdchem.Mol,
            building_blocks: str='US_stock',
            reaction_sites: list[int]=[],
            reaction_tags: list[str] | str=['amide coupling', 'amide', 'C-N bond formation', 'C-N',
                                            'alkylation', 'N-arylation', 'azole', 'amination'],
            rules: dict[str, tuple[int, int]]={
                'MW': (0, 500), # molecular weight
                'HBD': (0, 5), # hydrogen bond donors
                'HBA': (0, 10), # hydrogen bond acceptors
                'TPSA': (0, 200), # topological polar surface area
                'RotB': (0, 10), # rotatable bonds
                'Rings': (0, 10), # number of rings
                'ArRings': (0, 5), # number of aromatic rings
                'Chiral': (0, 5), # number of chiral centers
            },
            struct_rules: list[str]=[]
    ):
        '''
            Initialize the Enumerator object.

            Args:
                molecule (str): SMILES string or rdkit mol object. This molecule will be
                                enumerated with building blocks at the given reaction site.
                reaction_sites (list): list of atom indices to consider for the enumeration.
                                      If no reaction site is provided, any possible reaction
                                      site will be considered.
                bb_supplier (str): "US_stock", "EU_stock", "Global_stock", or "NoRush_stock". 
                                    A custom path to a file containing building blocks can 
                                    also be provided.
                reaction_tags (list): list of reaction tags to consider for the enumeration.
                rules (dict): dictionary containing the rules if the method is rules.
                                - MW: tuple (min, max) -- molecular weight
                                - HBD: tuple (min, max) -- hydrogen bond donors
                                - HBA: tuple (min, max) -- hydrogen bond acceptors
                                - TPSA: tuple (min, max) -- topological polar surface area
                                - RotB: tuple (min, max) -- rotatable bonds
                                - Rings: tuple (min, max) -- number of rings
                                - ArRings: tuple (min, max) -- number of aromatic rings
                                - Chiral: tuple (min, max) -- number of chiral centers
                struct_rules (list): list of structure-based rules. List of SMILES to include 
                                     in the building blocks.
        '''
        super().__init__(molecule, building_blocks, True)
        self.reaction_sites = reaction_sites
        self.rules = rules
        self.struct_rules = struct_rules
        self.reaction_tags = reaction_tags
        if isinstance(self.reaction_tags, str) and self.reaction_tags == 'all':
            self.reaction_tags = [tag for tag in [reaction.tags for reaction in self._reactions]]
            self.reactions = self._reactions
        else:
            self.reactions = [reaction for reaction in self._reactions 
                              if any(tag in reaction.tags for tag in self.reaction_tags)]

    def enumerate(self):
        '''
            Enumerate the molecule with building blocks.
        '''
        self._prepare_molecule()
        self._process_building_blocks()
        
        query_fp = self._get_fingerprint(self.molecule)
        self.enumerated_molecules = []
        for bb in tqdm(self._filtered_bb, desc='Enumerating building blocks', total=len(self._filtered_bb)):
            url = bb.GetProp('URL') if bb.HasProp('URL') else ''
            for reaction in self.reactions:
                products = reaction.run_syn(self._prepared_mol, bb)
                if products:
                    for product in products:
                        for p in product:
                            try:
                                p = Chem.MolFromSmiles(Chem.MolToSmiles(p))
                                product_fp = self._get_fingerprint(p)
                                tani_sim = utils.get_tani_sim_fp(query_fp, product_fp)
                            except:
                                print('Fingerprint calculation for a product failed! Skipping...', 
                                      flush=True)
                                continue
                            Enumeration = namedtuple(
                                'Enumeration', 
                                ['Product', 'Similarity_to_query', 'BB', 'Reaction_name', 'URL']
                            )
                            enum = Enumeration(Chem.MolToSmiles(p),
                                               round(tani_sim, 2),
                                               Chem.MolToSmiles(bb),
                                               reaction.name,
                                               url)
                            self.enumerated_molecules.append(enum)

    def get_results(self, as_dict: bool=False):
        '''
            Get the results as a pandas DataFrame.

            Args:
                as_dict (bool): return the results as a dictionary.

            Returns:
                pd.DataFrame: results.
        '''
        query_molecule_row = {'Product': Chem.MolToSmiles(self.molecule),
                              'Similarity_to_query': 1.0,
                              'BB': '',
                              'Reaction_name': '',
                              'URL': ''}

        if not self.enumerated_molecules:
            print('No enumerated molecules found!')
            df = pd.DataFrame([query_molecule_row])
            if as_dict:
                return df.to_dict(orient='records')
            return df
        
        column_names = ['Product', 'Similarity_to_query', 'BB', 'Reaction_name', 'URL']
        df = pd.DataFrame(self.enumerated_molecules, columns=column_names)
        df = df.drop_duplicates(subset=['Product'], keep='first', ignore_index=True)
        df = pd.concat([pd.DataFrame([query_molecule_row]), df], ignore_index=True)
        df = df.sort_values(by='Similarity_to_query', ascending=False, ignore_index=True)
        df = df.reset_index(drop=True)
        df['ID'] = df.index
        if as_dict:
            return df.to_dict(orient='records')
        return df

    def save_results(self, path: str='enumerated_molecules.csv'):
        '''
            Save the results to a file in CSV format.
            
            Columns: Product, Similarity_to_query, BB, Reaction_name

            Args:
                path (str): path to the file.
        '''
        df = self.get_results()
        df.to_csv(path, index=False)

    def set_rules(self, **kwargs):
        '''
            Set the rules for the building blocks.

            Args:
                kwargs: dictionary containing the rules.
                    MW: tuple (min, max) -- molecular weight
                    HBD: tuple (min, max) -- hydrogen bond donors
                    HBA: tuple (min, max) -- hydrogen bond acceptors
                    TPSA: tuple (min, max) -- topological polar surface area
                    RotB: tuple (min, max) -- rotatable bonds
                    Rings: tuple (min, max) -- number of rings
                    ArRings: tuple (min, max) -- number of aromatic rings
                    Chiral: tuple (min, max) -- number of chiral centers
        '''
        for key, value in kwargs.items():
            if key in self.rules:
                self.rules[key] = value
            else:
                raise ValueError(f'Invalid rule: {key}')

    def _process_building_blocks(self):
        '''
            Process the building blocks to filter out the ones that do not
            satisfy the rules.
        '''
        self._filtered_bb = []
        for bb in tqdm(self.bb_supplier, desc='Processing building blocks', total=len(self.bb_supplier)):
            if bb is not None:
                if self._check_rules(bb) and self._check_struct_rules(bb):
                    for rxn in self.reactions:
                        if rxn.is_reactant(bb):
                            self._filtered_bb.append(bb)
                            break
    
    def _prepare_molecule(self, protect_neighbors: bool=False):
        '''
            Prepare the molecule by adding protection to the atoms that are not 
            part of the reaction site. If protect_neighbors is set to True, the
            neighbors of the reaction site will be protected.

            Args:
                protect_neighbors (bool): protect the neighbors of the reaction site
        '''
        self._prepared_mol = Chem.MolFromSmiles(Chem.MolToSmiles(self.molecule))
        if self.reaction_sites:
            dont_protect = set()
            for atom in self._prepared_mol.GetAtoms():
                if atom.GetIdx() in self.reaction_sites:
                    dont_protect.add(atom.GetIdx())
                    if not protect_neighbors:
                        for neighbor in atom.GetNeighbors():
                            dont_protect.add(neighbor.GetIdx())
            for atom in self._prepared_mol.GetAtoms():
                if atom.GetIdx() not in dont_protect:
                    atom.SetProp('_protected', '1')
        else:
            print('No reaction sites provided! All atoms will be considered reactive.', flush=True)
            pass

    def _check_struct_rules(self, building_block: Chem.rdchem.Mol | str):
        '''
            Check if the building block satisfies the structure-based rules.

            Args:
                building_block: rdkit mol object or SMILES string.
        '''
        if isinstance(building_block, str):
            building_block = Chem.MolFromSmiles(building_block)

        if not self.struct_rules:
            return True
        for rule in self.struct_rules:
            if not building_block.HasSubstructMatch(Chem.MolFromSmarts(rule)):
                return False
        return True

    def _check_rules(self, building_block: Chem.rdchem.Mol | str):
        '''
            Check if the building block satisfies the rules.

            Args:
                building_block: rdkit mol or SMILES string.
        '''
        if isinstance(building_block, str):
            building_block = Chem.MolFromSmiles(building_block)

        for key, value in self.rules.items():
            if key == 'MW':
                if not value[0] <= Descriptors.MolWt(building_block) <= value[1]:
                    return False
            elif key == 'HBD':
                if not value[0] <= Descriptors.NumHDonors(building_block) <= value[1]:
                    return False
            elif key == 'HBA':
                if not value[0] <= Descriptors.NumHAcceptors(building_block) <= value[1]:
                    return False
            elif key == 'TPSA':
                if not value[0] <= Descriptors.TPSA(building_block) <= value[1]:
                    return False
            elif key == 'RotB':
                if not value[0] <= Descriptors.NumRotatableBonds(building_block) <= value[1]:
                    return False
            elif key == 'Rings':
                if not value[0] <= Descriptors.RingCount(building_block) <= value[1]:
                    return False
            elif key == 'ArRings':
                if not value[0] <= Descriptors.NumAromaticRings(building_block) <= value[1]:
                    return False
            elif key == 'Chiral':
                if not value[0] <= rdMolDescriptors.CalcNumAtomStereoCenters(building_block) <= value[1]:
                    return False
            else:
                raise ValueError(f'Invalid rule: {key}')
            
        return True
        

class MoleculeEnumerator(_BaseEnumerator):
    '''
        Molecule Enumerator class is used to enumerate a given molecule with building blocks.
    '''
    def __init__(
            self, 
            molecule, 
            building_blocks: str='US_stock', 
            reaction_tags: list[str]=['amide coupling', 'amide', 'C-N bond formation', 'C-N',
                                      'alkylation', 'N-arylation', 'azole', 'amination'],
            custom_comp_sites: list[tuple]=[],
            n_compositions: int=10,
            sim_threshold: float=0.5,
    ):
        '''
            Initialize the MoleculeEnumerator object.

            Args:
                molecule (str): SMILES string or rdkit mol object.
                bb_supplier (str): "US_stock", "EU_stock", "Global_stock", or "NoRush_stock". 
                                    A custom path to a file containing building blocks can 
                                    also be provided.
                reaction_tags (list): list of reaction tags to consider for the enumeration.
                custom_comp_sites (list(tuple)): list of tuples containing the atom indices for
                                                 splitting the molecule. Each tuple represents a
                                                 a composition site.
                n_compositions (int): number of compositions of the molecule to enumerate.
                sim_threshold (float): similarity threshold.
        '''
        super().__init__(molecule, building_blocks, True)
        self.reaction_tags = reaction_tags
        self.custom_comp_sites = custom_comp_sites
        self.n_compositions = n_compositions
        self.sim_threshold = sim_threshold
        if isinstance(self.reaction_tags, str) and self.reaction_tags == 'all':
            self.reaction_tags = [tag for tag in [reaction.tags for reaction in self._reactions]]
            self.reaction_tags = list(set(chain(*self.reaction_tags)))
            self.reactions = self._reactions
        else:
            self.reactions = [reaction for reaction in self._reactions 
                              if any(tag in reaction.tags for tag in self.reaction_tags)]

        self._compositions = [] # list of rxn based compositions of the molecule

    def enumerate(self):
        '''
            Enumerate the molecule with building blocks.
        '''
        self._prepare_molecule()
        if not self._compositions:
            self.enumerated_molecules = []
            return
        self._process_building_blocks()

        # enumerate the molecule with building blocks
        query_fp = self._get_fingerprint(self.molecule)
        self.enumerated_molecules = []
        counter = 0
        for composition in tqdm(self._filtered_bb, desc='Enumerating building blocks', total=len(self._filtered_bb)):
            if counter == self.n_compositions:
                break
            counter += 1
            for b1, b2 in iter_product(*composition):
                url1 = b1.GetProp('URL') if b1.HasProp('URL') else ''
                url2 = b2.GetProp('URL') if b2.HasProp('URL') else ''
                for reaction in self.reactions:
                    products = reaction.run_syn(b1, b2)
                    if products:
                        for product in products:
                            for p in product:
                                try:
                                    p = Chem.MolFromSmiles(Chem.MolToSmiles(p))
                                    product_fp = self._get_fingerprint(p)
                                    tani_sim = utils.get_tani_sim_fp(query_fp, product_fp)
                                except:
                                    print('Fingerprint calculation of a product failed! Skipping...', 
                                          flush=True)
                                    continue
                                # add the product to the list as a named tuple
                                Enumeration = namedtuple(
                                    'Enumeration', 
                                    ['Product', 'Similarity_to_query', 'BB1', 'BB2', 
                                     'Reaction_name', 'URL1', 'URL2']
                                )
                                enum = Enumeration(Chem.MolToSmiles(p),
                                                   round(tani_sim, 2),
                                                   Chem.MolToSmiles(b1),
                                                   Chem.MolToSmiles(b2),
                                                   reaction.name,
                                                   url1, url2)
                                self.enumerated_molecules.append(enum)

    def get_results(self, as_dict: bool=False):
        '''
            Get the results as a pandas DataFrame.

            Args:
                as_dict (bool): return the results as a dictionary.

            Returns:
                pd.DataFrame: results.
        '''
        query_molecule_row = {'Product': Chem.MolToSmiles(self.molecule),
                              'Similarity_to_query': 1.0,
                              'BB1': '',
                              'BB2': '',
                              'Reaction_name': '',
                              'URL1': '',
                              'URL2': ''}
        if not self.enumerated_molecules:
            print('No enumerated molecules found! ')
            df = pd.DataFrame([query_molecule_row])
            if as_dict:
                return df.to_dict(orient='records')
            return df
        
        column_names = ['Product', 'Similarity_to_query', 'BB1', 'BB2', 'Reaction_name', 'URL1', 'URL2']
        df = pd.DataFrame(self.enumerated_molecules, columns=column_names)
        df = df.drop_duplicates(subset=['Product'], keep='first', ignore_index=True)
        df = pd.concat([pd.DataFrame([query_molecule_row]), df], ignore_index=True)
        df = df.sort_values(by='Similarity_to_query', ascending=False, ignore_index=True)
        df = df.reset_index(drop=True)
        df['ID'] = df.index
        if as_dict:
            return df.to_dict(orient='records')
        return df

    def save_results(self, path: str='enumerated_molecules.csv'):
        '''
            Save the results to a file in CSV format.
            
            Columns: Product, Similarity_to_query, BB1, BB2, 
                Reaction_name, URL1, URL2

            Args:
                path (str): path to the file.
        '''
        df = self.get_results()
        df.to_csv(path, index=False)

    def _process_building_blocks(self, batch_size: int=10000):
        '''
            Process the building blocks to filter out the ones that do not
            satisfy the rules. This will create a list of building blocks
            for each composition of the molecule. The final list will be
            in the following format:
            ```
                [  
                    [[bb1, bb3, ...], [bb5, bb6, ...]],     # composition 1  
                    [[bb1, bb2, ...], [bb4, bb9, ...]],     # composition 2  
                    ...
                ]
            ```
        '''
        composition_fps = self._get_fingerprints(list(chain(*self._compositions)), return_np=False)
        stock_fps = self._get_fingerprints(self.bb_supplier, return_np=False)
        tani_sims = np.zeros((len(composition_fps), len(stock_fps)))
        for i in tqdm(range(0, len(stock_fps), batch_size), desc='Processing building blocks', total=len(stock_fps)//batch_size):
            batch_stock_fps = stock_fps[i:i+batch_size]
            if isinstance(composition_fps, list):
                batch_tani_sims = utils.get_batch_tani_sims_rdkit(composition_fps, batch_stock_fps)
            else:
                batch_tani_sims = np.asarray(utils.get_batch_tani_sims_jax(composition_fps, batch_stock_fps))
            tani_sims[:, i:i+batch_size] = batch_tani_sims
        tani_sims = tani_sims >= self.sim_threshold

        self._filtered_bb = self._get_mols_from_supplier(tani_sims)
                
    def _get_mols_from_supplier(self, mask=None):
        '''
            Get molecules from the supplier based on the mask.

            Args:
                mask (np.ndarray): boolean mask.

            Returns:
                list: list of rdkit mol objects.
        '''
        if mask is None:
            return [mol for mol in self.bb_supplier if mol is not None]
        
        mols_needed_idx = np.nonzero(mask)[1]
        mols_needed = {int(idx): self.bb_supplier[int(idx)] for idx in set(mols_needed_idx)}
        masked_mols = []
        for i in tqdm(range(0, len(mask), 2), desc='Loading filtered building blocks from source', total=len(mask)//2):
            mask_row1_idx = np.nonzero(mask[i])[0]
            mask_row2_idx = np.nonzero(mask[i+1])[0]
            if len(mask_row1_idx) == 0 or len(mask_row2_idx) == 0:
                masked_mols.append([[], []])
            else:
                masked_mols.append([[mols_needed[idx] for idx in mask_row1_idx], 
                                    [mols_needed[idx] for idx in mask_row2_idx]])
        return masked_mols
    
    def _prepare_molecule(self):
        '''
            Prepare the molecule by finding possible substructure compositions with
            respect to reaction template data or custom composition sites.
        '''
        if self.custom_comp_sites:
            for site in self.custom_comp_sites:
                product = self._split_molecule(site)
                for p in product:
                    try:
                        flag = Chem.SanitizeMol(p, catchErrors=True)
                        assert flag == Chem.rdmolops.SanitizeFlags.SANITIZE_NONE
                    except AssertionError:
                        print('Sanitization failed!')
                        continue
                self._compositions.append(product)
        else:
            for reaction in self._reactions:
                products = reaction.run_retro(self.molecule)
                if products:
                    for product in products:
                        for p in product:
                            try:
                                flag = Chem.SanitizeMol(p, catchErrors=True)
                                assert flag == Chem.rdmolops.SanitizeFlags.SANITIZE_NONE
                            except AssertionError:
                                print('Sanitization failed!')
                                continue
                        print(reaction.name)
                        self._compositions.append(product)
            
            self._remove_duplicate_compositions()

        self.print_compositions()

    def _split_molecule(self, split_site: tuple[int, int]):
        '''
            Splits the molecule at the given bond.

            Args:
                split_site: tuple of two integers, bond to split.

            Returns:
                tuple of rdkit mol objects, split molecules.
        '''
        # split the molecule
        with Chem.RWMol(self.molecule) as m:
            m.RemoveBond(split_site[0], split_site[1])
        m = m.GetMol()
        frags = Chem.GetMolFrags(m, asMols=True, sanitizeFrags=True)
        return frags
        
    def _remove_duplicate_compositions(self):
        '''
            Remove duplicate compositions.
        '''
        composition_idx_to_remove = []
        for i, comp_i in enumerate(self._compositions):
            for j, comp_j in enumerate(self._compositions):
                if i < j:
                    all_smiles = [Chem.MolToSmiles(substruct) for substruct in comp_i]
                    all_smiles.extend([Chem.MolToSmiles(substruct) for substruct in comp_j])
                    if len(set(all_smiles)) <= 2:
                        composition_idx_to_remove.append(i)
                        break
        
        for idx in sorted(composition_idx_to_remove)[::-1]:
            self._compositions.pop(idx)

    def print_compositions(self):
        if self._compositions:
            for i, composition in enumerate(self._compositions):
                smiles = [Chem.MolToSmiles(substruct) for substruct in composition]
                print(f'Composition {i}: {smiles}')
        else:
            print('No compositions found!')

