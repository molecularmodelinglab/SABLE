import io, re, requests, json
from functools import cache
import pandas as pd
from io import StringIO
from typing import Optional


class IDList:
    """
    A class to manage a list of IDs with a method to check for PubChem IDs.
    """
    
    def __init__(self, ids=None):
        """
        Initialize the IDList with an optional list of IDs.
        
        Args:
            ids (list, optional): Initial list of IDs. Defaults to empty list.
        """
        self.ids = ids if ids is not None else []
    
    def add_id(self, id_value):
        self.ids.append(id_value)
    
    def remove_id(self, id_value):
        if id_value in self.ids:
            self.ids.remove(id_value)
    
    def contains_pubchem(self):
        """
        Check if the list contains a PubChem ID and return the first one found.
        
        PubChem IDs follow the pattern: "PUBCHEM:XXXXXX" where X are digits.
        
        Returns:
            str or False: The first PubChem ID found, or False if none exists.
        """
        # Pattern to match PubChem IDs (case-insensitive)
        pubchem_pattern = re.compile(r'^PUBCHEM.COMPOUND:\d+$', re.IGNORECASE)
        
        for id_value in self.ids:
            if isinstance(id_value, str) and pubchem_pattern.match(id_value):
                return id_value
        
        return False
    
    def get_all_pubchem_ids(self):
        """
        Get all PubChem IDs in the list.
        
        Returns:
            list: List of all PubChem IDs found.
        """
        pubchem_pattern = re.compile(r'^PUBCHEM.COMPOUND:\d+$', re.IGNORECASE)
        return [id_value for id_value in self.ids 
                if isinstance(id_value, str) and pubchem_pattern.match(id_value)]
    
    def __len__(self):
        """Return the number of IDs in the list."""
        return len(self.ids)
    
    def __str__(self):
        """String representation of the IDList."""
        return f"IDList({self.ids})"
    
    def __repr__(self):
        """Developer representation of the IDList."""
        return f"IDList(ids={self.ids!r})"


def normalizer_alt_ids(id: str) -> IDList:
    """
    Uses Node Normalizer to return a set of a
    """
    return IDList([id])

def get_smiles_from_pubchem(pubchem_id: int) -> Optional[str]:
    print('extracting smiles from pubchem')
    """
    Retrieve the SMILES string for a chemical compound using its PubChem ID (CID).
    
    Args:
        pubchem_id (int): The PubChem Compound ID (CID)
    
    Returns:
        Optional[str]: The SMILES string if found, None if not found or error occurs
        
    Raises:
        ValueError: If the pubchem_id is not a positive integer
        requests.RequestException: If there's an error with the API request
    """
    if not isinstance(pubchem_id, int) or pubchem_id <= 0:
        raise ValueError("PubChem ID must be a positive integer")

    # PubChem REST API endpoint
    base_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    endpoint = f"{base_url}/compound/cid/{pubchem_id}/property/IsomericSMILES/JSON"
    print(endpoint)
    try:
        # Make the API request
        response = requests.get(endpoint)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        # Parse the JSON response
        data = response.json()
        
        # Extract the SMILES string
        smiles = data["PropertyTable"]["Properties"][0]["SMILES"]
        return smiles
        
    except requests.RequestException as e:
        print("API request error.")
        return None


@cache
def nameres(itemRequest:str) -> Optional[str]:
    print('resolving name')
    failed_counts = 0
    success = False
    while True:
        try:
            returned = (pd.read_json(StringIO(requests.get(itemRequest, timeout = 10).text)))
            return returned.curie[0]
        except Exception as e:
            print(e)
            failed_counts += 1
        if failed_counts >=5:
            raise ConnectionAbortedError

def identify(name: str, params: dict):
    """
    Args:
        name (str): string to be identified
        params (tuple): name resolver parameters to feed into get request
    
    Returns:
        resolvedName (list[str]): ID most closely matching string.

    """   
    itemRequest = (params['url']+
                   params['service']+
                   '?string='+
                   name+
                   '&autocomplete='+
                   str(params['autocomplete_setting']).lower()+
                   '&offset='+
                   str(params['offset'])+
                   '&limit='+
                   str(params['id_limit'])+
                   "&biolink_type="+
                   params['biolink_type'])

    return nameres(itemRequest)

def normalize(item: str) -> Optional[IDList]:
    print("normalizing")
    item_request = f"https://nodenormalization-sri.renci.org/1.5/get_normalized_nodes?curie={item}&conflate=true&drug_chemical_conflate=true&description=false&individual_types=false"    
    success = False
    failedCounts = 0
    while not success:
        try:
            response = requests.get(item_request)
            output = json.loads(response.text)
            primary_key = list(output.keys())[0]
            label = output[primary_key]['id']['label']
            alternate_ids = output[item]['equivalent_identifiers']
            returned_ids = list(item['identifier'] for item in alternate_ids)
            success = True
        except Exception as e:
            print(e)
            failedCounts += 1
        if failedCounts >= 5:
            return None
    return IDList(returned_ids)

def n2s(instring: str) -> Optional[str]:
    name_resolver_params = {
        "url": "https://name-resolution-sri.renci.org/",
        "service": "lookup",
        "autocomplete_setting": "true",
        "id_limit": "10",
        "offset": "0",
        "biolink_type": "ChemicalOrDrugOrTreatment",   
  }
    id = identify(instring, name_resolver_params)
    print(f"found ID {id}")
    alt_ids = normalize(id)


    smiles = None
    if alt_ids.contains_pubchem():
        print("found pubchem ids in alt_ids")
        i=0
        print(alt_ids.get_all_pubchem_ids())
        for item in alt_ids.get_all_pubchem_ids():
            try:
                print(f"extracting smiles from {item.replace('PUBCHEM.COMPOUND:', '')}")
                smiles = get_smiles_from_pubchem(int(item.replace("PUBCHEM.COMPOUND:","")))
                if smiles:
                    print(f"found smiles {smiles}")
                    return smiles
                else:
                    print("no smiles found for {item}")
            except Exception as e:
                print(e)
                raise ImportError

    return None
