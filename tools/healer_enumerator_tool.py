import os
import time
from pathlib import Path
from langchain.tools import BaseTool
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr
from typing import Type, List, Dict, Any, Union, Optional, Tuple, ClassVar
from rdkit import Chem
import pandas as pd
import requests

from schemas.errors import ToolError
from schemas.tool_schemas import EnumerationRequest, EnumerationResult

class HealerEnumeratorInput(BaseModel):
    """Input schema for the HealerEnumeratorTool."""
    molecule: str = Field(..., description="SMILES string of the molecule to enumerate.")
    healer_mode: Optional[Any] = Field(default="MoleculeHEALER", description="Enumeration mode to use (default: ' MoleculeHEALER').")
    n_compositions: Optional[int] = Field(default=10, description="Number of compositions to enumerate (default: 10).")
    sim_threshold: Optional[float] = Field(default=None, description="Similarity threshold for filtering results (default: 0.5).")
    reaction_tags: Optional[List[str]] = Field(default=None, description="List of reaction tags for enumeration (uses a default set if not provided).")
    bb_supplier: Optional[str] = Field(default="EU_stock", description="Building blocks source (default: 'EU_stock').")
    custom_comp_sites: Optional[List[Tuple]] = Field(default=None, description="Custom composition sites for splitting the molecule (default: empty list).")
    max_bbs_per_comp: Optional[int] = Field(default=5, description="Maximum number of building blocks per composition.")
    verbose: Optional[int] = Field(default=0, description="Verbosity level for the enumeration process.")


class HealerEnumeratorTool(BaseTool):
    """
    Enumerates a library of new molecules based on a starting molecule,
    using specified reactions and building blocks. Returns a dictionary
    mapping molecule IDs to their SMILES strings.
    """
    name: str = "HealerEnumerator"
    description: str = Field(default="Enumerates new molecules from a starting compound using HEALER.")
    args_schema: Type[BaseModel] = HealerEnumeratorInput
    healer_mode: str = Field(default="MoleculeHEALER", description="HEALER enumeration mode: MoleculeHEALER, FragmentHEALER, or SiteHEALER")
    execution_mode: str = Field(default="internal", description="HEALER execution mode: internal or api")
    endpoint: str = ""
    bb_source: str = "US_stock"
    reaction_tags: List[str] = Field(default_factory=lambda: ["all"])
    sim_threshold: float = 0.3
    max_bbs_per_frag: int = 10
    max_total_products: int = 8_000
    poll_interval_seconds: float = 2.0
    job_timeout_seconds: float = 900.0
    http_timeout_seconds: float = 30.0
    output_dir: str = "data/healer_outputs"
    
    _enumerator: Any = PrivateAttr(default=None)
    _session: requests.Session = PrivateAttr()
    _last_run_metadata: Dict[str, Any] = PrivateAttr(default_factory=dict)
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    STOCK: ClassVar[str] = "US_stock"
    def __init__(
        self,
        healer_mode: str = "MoleculeHEALER",
        execution_mode: Optional[str] = None,
        endpoint: Optional[str] = None,
        bb_source: Optional[str] = None,
        reaction_tags: Optional[List[str]] = None,
        sim_threshold: float = 0.3,
        max_bbs_per_frag: int = 10,
        max_total_products: int = 8_000,
        poll_interval_seconds: Optional[float] = None,
        job_timeout_seconds: Optional[float] = None,
        http_timeout_seconds: Optional[float] = None,
        output_dir: Optional[str] = None,
        session: Optional[requests.Session] = None,
        **kwargs,
    ):
        """
        Initialize HEALER with a specific mode: MoleculeHEALER, FragmentHEALER or SiteHEALER.
        """
        resolved_execution_mode = (execution_mode or os.getenv("HEALER_EXECUTION_MODE", "internal")).strip().lower()
        resolved_endpoint = (endpoint or os.getenv("HEALER_ENDPOINT", "")).rstrip("/")
        resolved_bb_source = bb_source or os.getenv("HEALER_STOCK", self.STOCK)
        super().__init__(
            healer_mode=healer_mode,
            execution_mode=resolved_execution_mode,
            endpoint=resolved_endpoint,
            bb_source=resolved_bb_source,
            reaction_tags=reaction_tags or ["all"],
            sim_threshold=sim_threshold,
            max_bbs_per_frag=max_bbs_per_frag,
            max_total_products=max_total_products,
            poll_interval_seconds=self._env_float("HEALER_POLL_INTERVAL_SECONDS", poll_interval_seconds, 2.0),
            job_timeout_seconds=self._env_float("HEALER_JOB_TIMEOUT_SECONDS", job_timeout_seconds, 900.0),
            http_timeout_seconds=self._env_float("HEALER_HTTP_TIMEOUT_SECONDS", http_timeout_seconds, 30.0),
            output_dir=output_dir or os.getenv("HEALER_OUTPUT_DIR", "data/healer_outputs"),
            **kwargs,
        )
        self._session = session or requests.Session()

        if self.execution_mode not in {"internal", "api"}:
            raise ValueError("HEALER_EXECUTION_MODE must be 'internal' or 'api'")
        if self.execution_mode == "api":
            if not self.endpoint:
                raise ValueError("HEALER_ENDPOINT is required when HEALER_EXECUTION_MODE=api")
            if self.healer_mode == "FragmentHEALER":
                raise ValueError("FragmentHEALER is not supported by the HEALER API")
            return

        self._initialize_internal_enumerator()

    @staticmethod
    def _env_float(name: str, explicit_value: Optional[float], default: float) -> float:
        if explicit_value is not None:
            return float(explicit_value)
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} must be numeric, got {raw_value!r}") from exc

    def _initialize_internal_enumerator(self) -> None:
        import healer.utils.rdkit_monkey_patch as rdkit_monkey_patch
        from healer import MoleculeHEALER, SiteHEALER, FragmentHEALER
        from healer.domain import get_repository

        _ = rdkit_monkey_patch
        mode = self.healer_mode
        if mode == "MoleculeHEALER":
            self._enumerator = MoleculeHEALER(
                bb_source=self.bb_source,
                reaction_tags=self._internal_reaction_tags(),
                sim_threshold=self.sim_threshold,
                verbose=2,
                max_bbs_per_frag=self.max_bbs_per_frag,
                bb_repository=get_repository(self.bb_source)
            )
        elif mode == "FragmentHEALER":
            self._enumerator = FragmentHEALER(
                bb_source=self.bb_source,
                reaction_tags=self._internal_reaction_tags(),
                max_bbs_per_frag=self.max_bbs_per_frag,
                sim_threshold=self.sim_threshold,
                verbose=2,
                bb_repository=get_repository(self.bb_source)
            )
        elif mode == "SiteHEALER":
            self._enumerator = SiteHEALER(
                bb_source=self.bb_source,
                reaction_tags=self._internal_reaction_tags(),
                rules={
                    'MW': (0, 500),
                    'HBD': (0, 5),
                    'HBA': (0, 10),
                    'TPSA': (0, 200),
                    'RotB': (0, 10),
                    'Rings': (0, 10),
                    'ArRings': (0, 5),
                    'Chiral': (0, 5)
                },
                struct_rules=[],
                verbose=2,
                bb_repository=get_repository(self.bb_source)
            )
        else:
            raise ValueError(f"Invalid HEALER mode. Got {mode}, need one of MoleculeHEALER, FragmentHEALER or SiteHEALER")

    def _internal_reaction_tags(self) -> Union[str, List[str]]:
        return "all" if self.reaction_tags == ["all"] else self.reaction_tags

    def _run(
        self,
        molecule: str,
        n_compositions: Optional[int] = 100,
        max_evals_per_comp: Optional[int] = 500_000,
        randomize_compositions: Optional[bool] = True,
        random_seed: Optional[int] = 42,
        custom_split_sites: Optional[List[Tuple]] = None,
        retro_tree_depth: Optional[int] = 2,
        min_frag_size: Optional[int] = 3, #2,
        reactive_sites: Optional[List[int]] = None,
    ) -> Union[Dict[str, str], str]:
        try:
            started_at = time.perf_counter()
            if self.execution_mode == "api":
                result = self._run_api(
                    molecule=molecule,
                    n_compositions=n_compositions or 100,
                    reactive_sites=reactive_sites,
                )
                elapsed = time.perf_counter() - started_at
                print(f"HealerEnumeratorTool generated {len(result)} molecules in {elapsed:.2f}s.")
                return result

            self._last_run_metadata = {"execution_mode": "internal"}
            if self.healer_mode == "MoleculeHEALER":
                self._enumerator.set_query_mol(
                    query_mol=molecule,
                    n_compositions=n_compositions,
                    randomize_compositions=randomize_compositions,
                    random_seed=random_seed,
                    custom_split_sites=custom_split_sites,
                    retro_tree_depth=retro_tree_depth,
                    min_frag_size=min_frag_size
                )
            elif self.healer_mode == "FragmentHEALER":
                self._enumerator.set_query_mol(
                    query_mol=molecule,
                )
            elif self.healer_mode == "SiteHEALER":
                self._enumerator.set_query_mol(
                    query_mol=molecule,
                    reactive_sites=reactive_sites,
                )

            self._enumerator.enumerate(
                optimizer=None,
                max_total_products=self.max_total_products,
                # max_products_per_comp=2_000,
                max_evals_per_comp=max_evals_per_comp
                )
            
            results_df = self._enumerator.get_results(calc_similarity=True, calc_properties=False)
            results_df.drop_duplicates(subset='Product', inplace=True)
            
            if self.bb_source != "test":
                results_df = results_df[results_df['Similarity_to_query'] >= 0.25]

            if results_df.empty:
                return "No molecules were generated that met the criteria."

            enumerated_molecules_list = results_df['Product'].to_list()

            if not enumerated_molecules_list:
                return "No molecules were generated that met the criteria."

            # Format the output as a dictionary of {id: smiles}
            enumerated_molecules_dict = {f"mol_{i}": smi for i, smi in enumerate(enumerated_molecules_list)}
            elapsed = time.perf_counter() - started_at
    
            print(
                f"HealerEnumeratorTool generated {len(enumerated_molecules_dict)} molecules "
                f"in {elapsed:.2f}s."
            )
            return enumerated_molecules_dict

        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Error in HealerEnumeratorTool: {e}", tool="healer", code="EXCEPTION")

    def _run_api(
        self,
        molecule: str,
        n_compositions: int,
        reactive_sites: Optional[List[int]],
    ) -> Dict[str, str]:
        route = "/enumerate/site" if self.healer_mode == "SiteHEALER" else "/enumerate/molecule"
        payload: Dict[str, Any] = {
            "molecule": molecule,
            "bb_source": self.bb_source,
            "reaction_tags": self.reaction_tags,
            "max_bbs_per_frag": self.max_bbs_per_frag,
            "n_compositions": n_compositions,
            "max_total_products": self.max_total_products,
        }
        if self.healer_mode == "SiteHEALER" and reactive_sites is not None:
            payload["reactive_sites"] = reactive_sites

        submitted = self._request_json("POST", route, json=payload)
        job_id = submitted.get("job_id")
        if not job_id:
            raise ToolError("HEALER API submission did not return a job_id", tool="healer", code="API_BAD_RESPONSE")
        if str(submitted.get("status", "")).strip().lower() != "submitted":
            raise ToolError(
                f"HEALER API submission returned unexpected status {submitted.get('status')!r}",
                tool="healer",
                code="API_BAD_RESPONSE",
                details={"job_id": str(job_id)},
            )

        response = self._poll_api_job(str(job_id))
        result = response.get("result")
        complete_rows = result.get("complete") if isinstance(result, dict) else None
        if not isinstance(complete_rows, list):
            raise ToolError("HEALER API success response is missing result.complete", tool="healer", code="API_BAD_RESPONSE")

        results_df = pd.DataFrame(complete_rows)
        if "Product" not in results_df.columns:
            raise ToolError("HEALER API result.complete is missing Product", tool="healer", code="API_BAD_RESPONSE")

        output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_job_id = "".join(character for character in str(job_id) if character.isalnum() or character in {"-", "_"})
        csv_path = output_dir / f"molecular_enumerations_{safe_job_id}.csv"
        results_df.to_csv(csv_path, index=False)

        products = results_df["Product"].dropna().astype(str).drop_duplicates().tolist()
        if not products:
            raise ToolError("No molecules were generated that met the criteria.", tool="healer", code="ENUMERATOR_FAILED")

        self._last_run_metadata = {
            "execution_mode": "api",
            "job_id": str(job_id),
            "endpoint": self.endpoint,
            "csv_path": str(csv_path),
            "source_row_count": len(results_df),
            "stats": result.get("stats", {}),
        }
        return {f"mol_{index}": smiles for index, smiles in enumerate(products)}

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self.endpoint}{path}"
        try:
            response = self._session.request(method, url, timeout=self.http_timeout_seconds, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ToolError(f"HEALER API request failed: {exc}", tool="healer", code="API_REQUEST_FAILED") from exc
        except ValueError as exc:
            raise ToolError(f"HEALER API returned invalid JSON from {url}", tool="healer", code="API_BAD_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise ToolError(f"HEALER API returned a non-object response from {url}", tool="healer", code="API_BAD_RESPONSE")
        return payload

    def _poll_api_job(self, job_id: str) -> Dict[str, Any]:
        deadline = time.monotonic() + self.job_timeout_seconds

        while True:
            response = self._request_json("GET", f"/jobs/{job_id}")
            status = str(response.get("status", "")).strip().lower()
            if status == "success":
                return response
            if status == "failure":
                message = response.get("error") or "HEALER API job failed"
                raise ToolError(str(message), tool="healer", code="API_JOB_FAILED", details={"job_id": job_id})
            if status != "progress":
                raise ToolError(
                    f"HEALER API returned unknown job status {response.get('status')!r}",
                    tool="healer",
                    code="API_BAD_RESPONSE",
                    details={"job_id": job_id},
                )
            if time.monotonic() >= deadline:
                raise ToolError(
                    f"Timed out waiting for HEALER API job {job_id}",
                    tool="healer",
                    code="API_JOB_TIMEOUT",
                    details={"job_id": job_id},
                )
            if self.poll_interval_seconds > 0:
                time.sleep(self.poll_interval_seconds)

    def enumerate(self, request: EnumerationRequest) -> EnumerationResult:
        """Run HEALER through the shared enumeration request/result contract."""
        result = self._run(
            molecule=request.starting_smiles,
            n_compositions=request.max_molecules,
        )
        if isinstance(result, str):
            raise ToolError(result, tool="healer", code="ENUMERATOR_FAILED")

        strategy = request.strategy.value if hasattr(request.strategy, "value") else str(request.strategy)
        return EnumerationResult(
            molecules={str(mol_id): str(smiles) for mol_id, smiles in result.items()},
            count=len(result),
            strategy_used=strategy,
            metadata={
                "tool_id": "healer",
                "healer_mode": self.healer_mode,
                "starting_smiles": request.starting_smiles,
                **self._last_run_metadata,
            },
        )

    @staticmethod
    def validate_smiles(smiles_string) -> bool:
        """Return True iff smiles parses and sanitizes, False otherwise (no exceptions)."""
        try:
            if not isinstance(smiles_string, str) or not smiles_string:
                return False
            # Avoid sanitization at parse time; sanitize explicitly with catchErrors
            mol = Chem.MolFromSmiles(smiles_string, sanitize=False)
            if mol is None:
                return False
            try:
                Chem.SanitizeMol(mol, catchErrors=True)
            except Exception:
                return False
            if '.' in smiles_string:
                try:
                    frags = Chem.GetMolFrags(mol, asMols=True)
                    if len(frags) > 1:
                        return False
                except Exception:
                    return False
            return True
        except Exception:
            return False


    async def _arun(self, **kwargs):
        raise NotImplementedError("HealerEnumeratorTool does not support async")
