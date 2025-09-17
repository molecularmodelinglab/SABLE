'''
    Reaction class to define chemical reacitons using rdkit.
'''
import inspect

from rdkit import Chem
from rdkit.Chem import Descriptors, rdChemReactions


# TODO: Implement a method to extract reaction templates from a given reaction

class ReactionTemplate21:
    '''
        Wraps rdkit reaction functions to define chemical reactions.
        This class is specifically designed to handle reactions with
        2 reactants and 1 product. If the reaction has more than 2 reactants
        or more than 1 product, the functions may not work as expected.
    '''
    def __init__(
            self,
            name: str,
            reaction_smarts: str,
            retro_smarts: str,
            display_smarts: str = None,
            rhs_classes: list[int] = tuple(),
            tags: list[str] = tuple(),
            description: str = "",
            long_name: str = None,
            tier: int = None
    ):
        '''
            Constructor for the Reaction class.

            Args:
                name: str, name of the reaction.
                kwargs: additional properties of the reaction.
                    reaction_smarts: str, SMARTS string for the reaction. Same as syn_smarts.
                    retro_smarts: str, SMARTS string for the retro reaction.
                    display_smarts: str, SMARTS string for the reaction.
                    description: str, description of the reaction.
                    long_name: str, long name of the reaction.
                    rhs_classes: list of int, reaction classes.
                    tags: list of str, tags for the reaction.
                    tier: int, tier of the reaction.
        '''
        self.name = name
        self.reaction_smarts = reaction_smarts
        self.retro_smarts = retro_smarts
        self.display_smarts = display_smarts
        self.rhs_classes = rhs_classes
        self.tags = tags
        self.description = description
        self.long_name = long_name
        self.tier = tier

        self.sanitized_ = False
        self._reaction = rdChemReactions.ReactionFromSmarts(reaction_smarts)
        self._reaction.Initialize()

        # check if a reaction is valid
        try:
            _ = rdChemReactions.SanitizeRxn(self._reaction)
            self._reaction.RemoveUnmappedReactantTemplates(0.1)
            self._reaction.RemoveUnmappedProductTemplates(0.1)
            if len(self.get_reactants()) == 2 and len(self.get_products()) == 1:
                self.sanitized_ = True
        except:
            self.sanitized_ = False

    @classmethod
    def from_reaction_json(cls, name: str, reaction_json: dict):
        cls_parameters = [
            p.name for p in inspect.signature(cls.__init__).parameters.values()
            if p.name != "self"
        ]

        valid_params = {key: val for key, val in reaction_json.items() if key in cls_parameters}

        # check for the fact that reaction_smarts could be called syn_smarts
        if "syn_smarts" in reaction_json.keys():
            valid_params["reaction_smarts"] = reaction_json["syn_smarts"]

        # add in the name
        valid_params["name"] = name

        return ReactionTemplate21(**valid_params)

    def get_reaction_smarts(self):
        '''
            Returns the reaction SMARTS string.
        '''
        return self._reaction_smarts
    
    def set_reaction_smarts(self, reaction_smarts):
        '''
            Sets the reaction SMARTS string.
        '''
        self._reaction_smarts = reaction_smarts
        self._reaction = rdChemReactions.ReactionFromSmarts(reaction_smarts)
        try:
            flags = rdChemReactions.SanitizeRxn(self._reaction)
            self._reaction.RemoveUnmappedReactantTemplates(0.1)
            self._reaction.RemoveUnmappedProductTemplates(0.1)
            if len(self.get_reactants()) == 2 and len(self.get_products()) == 1:
                self.sanitized_ = True
        except:
            self.sanitized_ = False

    def get_rdkit_reaction_object(self):
        return self._reaction

    def __str__(self):
        return self.name
    
    def __repr__(self):
        return self.name
    
    def __hash__(self):
        return hash(rdChemReactions.ReactionToSmiles(self._reaction, canonical=True))
    
    def get_reactants(self):
        '''
            Returns the reactants of the reaction sorted by molecular weight 
            in descending order.
        '''
        return sorted(list(self._reaction.GetReactants()), key=lambda x: Descriptors.MolWt(x), reverse=True)
    
    def get_products(self):
        '''
            Returns the products of the reaction sorted by molecular weight 
            in descending order.
        '''
        return sorted(list(self._reaction.GetProducts()), key=lambda x: Descriptors.MolWt(x), reverse=True)

    def get_reactants_smarts(self):
        '''
            Returns the SMARTS of the reactants.
        '''
        return [Chem.MolToSmarts(reactant) for reactant in self.get_reactants()]
    
    def get_products_smarts(self):
        '''
            Returns the SMARTS of the products.
        '''
        return [Chem.MolToSmarts(product) for product in self.get_products()]
    
    def get_reactants_smiles(self):
        '''
            Returns the SMILES of the reactants.
        '''
        return [Chem.MolToSmiles(reactant) for reactant in self.get_reactants()]
    
    def get_products_smiles(self):
        '''
            Returns the SMILES of the products.
        '''
        return [Chem.MolToSmiles(product) for product in self.get_products()]
    
    def is_valid(self):
        '''
            Returns True if the reaction template is valid.
        '''
        return self.sanitized_
    
    def is_reactant(self, mol):
        '''
            Returns True if the molecule is a reactant in the reaction.
        '''
        return self._reaction.IsMoleculeReactant(mol)
    
    def is_product(self, mol):
        '''
            Returns True if the molecule is a product in the reaction.
        '''
        return self._reaction.IsMoleculeProduct(mol)
    
    def run_syn(self, *reactants):
        '''
            Runs the reaction on the reactants and returns the products.
        '''
        products1 = self._reaction.RunReactants(list(reactants), maxProducts=10)
        products2 = self._reaction.RunReactants(list(reactants[::-1]), maxProducts=10)
        return products1 + products2

    def run_retro(self, *products):
        '''
            Runs the retro reaction on the products and returns the reactants.
        '''
        retro_reaction = rdChemReactions.ReactionFromSmarts(self.retro_smarts)
        return retro_reaction.RunReactants(list(products), maxProducts=10)
