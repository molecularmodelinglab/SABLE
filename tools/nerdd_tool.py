"""NERDD (Next-generation E-Resource for Drug Discovery) characterization tool.

NERDD (https://nerdd.univie.ac.at) hosts a family of QSAR models behind a single
REST API. Unlike STOPLIGHT, NERDD is job based: a job is submitted for a batch of
molecules, polled until it completes, and the results are then read page by page.

Each NERDD module is exposed to SABLE as its own characterizer tool id
(``nerdd_cypstrate``, ``nerdd_cyplebrity``, ...) so the registry can pick exactly
the module(s) needed for the requested target properties. All of them share this
implementation and differ only by their :class:`NerddModuleSpec`.

Module result schemas were taken from each module's ``nerdd.yml`` configuration
(https://github.com/molinfo-vienna). Raw module outputs are converted here into
the canonical SABLE property ids declared in ``config/properties.yml`` so that
the optimizer always receives plain floats.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Type

import requests
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from schemas.tool_schemas import CharacterizationRequest, CharacterizationResult

DEFAULT_BASE_URL = "https://nerdd.univie.ac.at/api"

# NERDD rejects a new submission with HTTP 429 while a previously submitted job is
# still being initialized, so submissions are retried with a short backoff.
_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
# Status codes meaning the POST route itself is unavailable, so the documented GET
# route should be tried. Client errors such as 400/422 describe a genuinely bad
# request and must surface instead of being retried as a different method.
_POST_FALLBACK_STATUS_CODES = frozenset({404, 405, 415})

_TERMINAL_OK_STATUS = "completed"
_TERMINAL_FAILED_STATUS = "failed"


class NerddToolError(RuntimeError):
    """Raised when a NERDD job cannot be submitted, completed, or parsed."""


def _as_float(value: Any) -> Optional[float]:
    """Coerce a NERDD result value to float, dropping nulls and non-finite values."""

    if value is None or isinstance(value, bool):
        return float(value) if isinstance(value, bool) else None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _collect_floats(rows: Sequence[Dict[str, Any]], key: str) -> List[float]:
    values = [_as_float(row.get(key)) for row in rows]
    return [value for value in values if value is not None]


def _molecule_row(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the single row describing a molecule-level prediction."""

    return rows[0] if rows else {}


def _map_numeric_fields(
    rows: Sequence[Dict[str, Any]],
    field_map: Dict[str, str],
    aggregate: Optional[Tuple[str, Callable[[List[float]], float]]] = None,
) -> Dict[str, float]:
    """Map molecule-level numeric result fields onto canonical property ids."""

    row = _molecule_row(rows)
    properties: Dict[str, float] = {}
    for source_field, property_id in field_map.items():
        value = _as_float(row.get(source_field))
        if value is not None:
            properties[property_id] = value

    if aggregate is not None:
        property_id, reducer = aggregate
        values = [properties[name] for name in field_map.values() if name in properties]
        if values:
            properties[property_id] = float(reducer(values))

    return properties


def _encode_substrate_label(value: Any) -> Optional[float]:
    """Encode a CYPstrate class label as a float.

    ``Substrate`` -> 1.0, ``Non-substrate`` -> 0.0. ``No prediction`` (returned when
    the consensus model abstains) is reported as missing rather than as a value, so
    an abstention is never optimized against as if it were a negative result.
    """

    if value is None:
        return None
    label = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    if not label:
        return None
    if label.startswith("non-substrate"):
        return 0.0
    if label.startswith("substrate"):
        return 1.0
    return None


def _extract_cyplebrity(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    return _map_numeric_fields(
        rows,
        {
            "prediction_1": "cyp1a2_inhibition",
            "prediction_2": "cyp2c9_inhibition",
            "prediction_3": "cyp2c19_inhibition",
            "prediction_4": "cyp2d6_inhibition",
            "prediction_5": "cyp3a4_inhibition",
        },
        aggregate=("cyp_inhibition_risk", max),
    )


_CYPSTRATE_FIELD_MAP = {
    "prediction_1a2": "cyp1a2_substrate",
    "prediction_2a6": "cyp2a6_substrate",
    "prediction_2b6": "cyp2b6_substrate",
    "prediction_2c8": "cyp2c8_substrate",
    "prediction_2c9": "cyp2c9_substrate",
    "prediction_2c19": "cyp2c19_substrate",
    "prediction_2d6": "cyp2d6_substrate",
    "prediction_2e1": "cyp2e1_substrate",
    "prediction_3a4": "cyp3a4_substrate",
}


def _extract_cypstrate(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    row = _molecule_row(rows)
    properties: Dict[str, float] = {}
    for source_field, property_id in _CYPSTRATE_FIELD_MAP.items():
        encoded = _encode_substrate_label(row.get(source_field))
        if encoded is not None:
            properties[property_id] = encoded

    resolved = [properties[name] for name in _CYPSTRATE_FIELD_MAP.values() if name in properties]
    properties["cyp_substrate_coverage"] = float(len(resolved))

    # The consensus model abstains ("No prediction") whenever its classifiers disagree,
    # and abstentions are common. Summing only the resolved isozymes would make an
    # uncertain molecule indistinguishable from a genuinely clean one, and because the
    # count is minimized the optimizer would actively prefer molecules the model could
    # not call. The aggregate is therefore only reported when every isozyme resolved;
    # cyp_substrate_coverage always reports how many did.
    if len(resolved) == len(_CYPSTRATE_FIELD_MAP):
        properties["cyp_substrate_count"] = float(sum(resolved))
    return properties


def _extract_hitdexter(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    return _map_numeric_fields(
        rows,
        {
            "prediction_1": "promiscuity_target_based",
            "prediction_2": "promiscuity_target_based_high",
            "prediction_3": "promiscuity_cell_based",
            "prediction_4": "promiscuity_cell_based_high",
            "prediction_5": "promiscuity_cell_based_extended",
            "prediction_6": "promiscuity_cell_based_extended_high",
        },
        aggregate=("promiscuity_risk", max),
    )


def _extract_np_scout(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    return _map_numeric_fields(rows, {"probability": "np_likeness"})


def _extract_skin_doctor(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    return _map_numeric_fields(
        rows,
        {
            "p_sens_class": "skin_sensitization_probability",
            "p_nonsens_class": "skin_nonsensitization_probability",
        },
    )


def _aggregate_som_rows(
    rows: Sequence[Dict[str, Any]],
    max_property: str,
    mean_property: str,
    count_property: str,
    extra_mean_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """Reduce per-atom site-of-metabolism rows to molecule-level properties."""

    probabilities = _collect_floats(rows, "prediction")
    properties: Dict[str, float] = {}

    if probabilities:
        properties[max_property] = max(probabilities)
        properties[mean_property] = sum(probabilities) / len(probabilities)

    binary_values = [row.get("prediction_binary") for row in rows]
    flags = [bool(value) for value in binary_values if value is not None]
    if flags:
        properties[count_property] = float(sum(flags))

    for source_field, property_id in (extra_mean_fields or {}).items():
        values = _collect_floats(rows, source_field)
        if values:
            properties[property_id] = sum(values) / len(values)

    return properties


def _extract_fame3r(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    return _aggregate_som_rows(
        rows,
        max_property="som_max_probability",
        mean_property="som_mean_probability",
        count_property="som_count",
    )


def _extract_awesom(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    return _aggregate_som_rows(
        rows,
        max_property="awesom_som_max_probability",
        mean_property="awesom_som_mean_probability",
        count_property="awesom_som_count",
        extra_mean_fields={"total_uncertainty": "awesom_total_uncertainty"},
    )


@dataclass(frozen=True)
class NerddModuleSpec:
    """Describes one NERDD module and how its results map onto SABLE properties."""

    module: str
    label: str
    level: str  # "molecule" or "atom"
    extractor: Callable[[Sequence[Dict[str, Any]]], Dict[str, float]]
    properties: Tuple[str, ...]
    default_params: Dict[str, Any] = field(default_factory=dict)
    batch_size: int = 50
    # NERDD's own module id when it differs from ``module`` (e.g. "np-scout").
    api_id: Optional[str] = None

    @property
    def endpoint(self) -> str:
        return self.api_id or self.module


NERDD_MODULES: Dict[str, NerddModuleSpec] = {
    "cypstrate": NerddModuleSpec(
        module="cypstrate",
        label="CYPstrate",
        level="molecule",
        extractor=_extract_cypstrate,
        properties=(
            *_CYPSTRATE_FIELD_MAP.values(),
            "cyp_substrate_count",
            "cyp_substrate_coverage",
        ),
        default_params={"prediction_mode": "best_performance"},
        batch_size=50,
    ),
    "cyplebrity": NerddModuleSpec(
        module="cyplebrity",
        label="CYPlebrity",
        level="molecule",
        extractor=_extract_cyplebrity,
        properties=(
            "cyp1a2_inhibition",
            "cyp2c9_inhibition",
            "cyp2c19_inhibition",
            "cyp2d6_inhibition",
            "cyp3a4_inhibition",
            "cyp_inhibition_risk",
        ),
        batch_size=50,
    ),
    "hitdexter3": NerddModuleSpec(
        module="hitdexter3",
        label="Hit Dexter 3",
        level="molecule",
        extractor=_extract_hitdexter,
        properties=(
            "promiscuity_target_based",
            "promiscuity_target_based_high",
            "promiscuity_cell_based",
            "promiscuity_cell_based_high",
            "promiscuity_cell_based_extended",
            "promiscuity_cell_based_extended_high",
            "promiscuity_risk",
        ),
        batch_size=50,
    ),
    "np_scout": NerddModuleSpec(
        module="np_scout",
        label="NP-Scout",
        level="molecule",
        extractor=_extract_np_scout,
        properties=("np_likeness",),
        # NERDD rejects jobs that leave its boolean parameters unset.
        default_params={"contour_lines": False, "quality": "low"},
        batch_size=50,
        api_id="np-scout",
    ),
    "skin_doctor_cp": NerddModuleSpec(
        module="skin_doctor_cp",
        label="Skin Doctor CP",
        level="molecule",
        extractor=_extract_skin_doctor,
        properties=(
            "skin_sensitization_probability",
            "skin_nonsensitization_probability",
        ),
        batch_size=25,
        api_id="skin-doctor-cp",
    ),
    "fame3r": NerddModuleSpec(
        module="fame3r",
        label="FAME3R",
        level="atom",
        extractor=_extract_fame3r,
        properties=("som_max_probability", "som_mean_probability", "som_count"),
        default_params={"metabolism_subset": "all", "fame_score": False, "shannon_entropy": False},
        batch_size=10,
    ),
    "awesom": NerddModuleSpec(
        module="awesom",
        label="AweSOM",
        level="atom",
        extractor=_extract_awesom,
        properties=(
            "awesom_som_max_probability",
            "awesom_som_mean_probability",
            "awesom_som_count",
            "awesom_total_uncertainty",
        ),
        batch_size=10,
    ),
}


class NerddToolInput(BaseModel):
    """Input for the NERDD API tool."""

    precision: int = Field(default=2, description="Unused; kept for characterizer parity.")
    search_space: Optional[Dict[str, str]] = Field(
        default=None, description="Mapping of molecule IDs to their SMILES strings."
    )
    ids_to_process: Optional[List[str]] = Field(
        default=None, description="Molecule IDs to characterize."
    )


class NerddTool(BaseTool):
    """Characterizes molecules with one NERDD module."""

    name: str = Field(default="NERDD")
    description: str = Field(
        default=(
            "Predicts ADMET-style properties (CYP inhibition and substrate liability, "
            "sites of metabolism, promiscuity, skin sensitization, natural product "
            "likeness) for a batch of molecules using the NERDD web service."
        )
    )
    args_schema: Type[BaseModel] = NerddToolInput

    module: str = Field(default="cypstrate", description="NERDD module id, e.g. 'cypstrate'.")
    base_url: str = Field(default_factory=lambda: os.getenv("NERDD_BASE_URL", DEFAULT_BASE_URL))
    job_params: Dict[str, Any] = Field(default_factory=dict)
    batch_size: Optional[int] = Field(default=None)
    request_timeout: float = Field(default_factory=lambda: float(os.getenv("NERDD_TIMEOUT", "60")))
    poll_interval: float = Field(
        default_factory=lambda: float(os.getenv("NERDD_POLL_INTERVAL", "5"))
    )
    max_wait_seconds: float = Field(
        default_factory=lambda: float(os.getenv("NERDD_MAX_WAIT", "900"))
    )
    max_retries: int = Field(default=4)

    # ------------------------------------------------------------------ helpers

    @property
    def spec(self) -> NerddModuleSpec:
        try:
            return NERDD_MODULES[self.module]
        except KeyError as exc:
            raise NerddToolError(
                f"Unknown NERDD module {self.module!r}. "
                f"Known modules: {', '.join(sorted(NERDD_MODULES))}"
            ) from exc

    @property
    def provided_properties(self) -> Tuple[str, ...]:
        return self.spec.properties

    def _effective_batch_size(self) -> int:
        configured = self.batch_size or int(os.getenv("NERDD_BATCH_SIZE", "0")) or self.spec.batch_size
        return max(1, int(configured))

    def _params(self) -> Dict[str, Any]:
        params = dict(self.spec.default_params)
        params.update(self.job_params or {})
        return params

    def _module_url(self, *parts: str) -> str:
        base = self.base_url.rstrip("/")
        suffix = "/".join(str(part).strip("/") for part in parts if str(part))
        return f"{base}/{self.spec.endpoint}/jobs" + (f"/{suffix}" if suffix else "")

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Issue a request, retrying transient failures and NERDD's 429 backpressure."""

        kwargs.setdefault("timeout", self.request_timeout)
        kwargs.setdefault("headers", {"Accept": "application/json"})

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = requests.request(method, url, **kwargs)
            except requests.exceptions.RequestException as exc:
                last_error = exc
            else:
                if response.status_code not in _RETRY_STATUS_CODES:
                    return response
                last_error = NerddToolError(
                    f"NERDD returned HTTP {response.status_code} for {url}: {response.text[:200]}"
                )

            if attempt < self.max_retries - 1:
                time.sleep(self.poll_interval * (attempt + 1))

        raise NerddToolError(f"NERDD request to {url} failed: {last_error}")

    # ------------------------------------------------------------------ job API

    def _create_job(self, smiles_list: Sequence[str]) -> str:
        """Submit a batch of molecules and return the NERDD job id."""

        payload: Dict[str, Any] = {"inputs": list(smiles_list), **self._params()}
        url = self._module_url()

        response = self._request("POST", url, data=payload)
        if response.status_code in _POST_FALLBACK_STATUS_CODES:
            # Older/proxied deployments only expose the documented GET route.
            response = self._request("GET", url, params=payload)

        if response.status_code == 403:
            raise NerddToolError(
                f"NERDD rejected the job for module {self.spec.module}: the anonymous "
                f"quota is exhausted ({response.text[:200]}). Reduce NERDD_BATCH_SIZE, "
                f"use fewer NERDD modules per campaign, or retry later."
            )
        if response.status_code >= 400:
            raise NerddToolError(
                f"NERDD job creation failed for module {self.spec.module} "
                f"(HTTP {response.status_code}): {response.text[:300]}"
            )

        try:
            job = response.json()
        except ValueError as exc:
            raise NerddToolError(f"NERDD returned a non-JSON job response: {exc}") from exc

        job_id = job.get("id")
        if not job_id:
            raise NerddToolError(f"NERDD job response did not contain an id: {job}")
        return str(job_id)

    def _wait_for_job(self, job_id: str) -> Dict[str, Any]:
        """Poll a job until it completes, fails, or the wait budget runs out."""

        url = self._module_url(job_id)
        deadline = time.monotonic() + self.max_wait_seconds
        job: Dict[str, Any] = {}

        while True:
            response = self._request("GET", url)
            if response.status_code >= 400:
                raise NerddToolError(
                    f"NERDD job {job_id} could not be read (HTTP {response.status_code})"
                )
            job = response.json()
            status = str(job.get("status", "")).lower()

            if status == _TERMINAL_OK_STATUS:
                return job
            if status == _TERMINAL_FAILED_STATUS:
                raise NerddToolError(f"NERDD job {job_id} failed for module {self.spec.module}")
            if time.monotonic() >= deadline:
                raise NerddToolError(
                    f"NERDD job {job_id} did not finish within {self.max_wait_seconds:.0f}s "
                    f"(last status: {status or 'unknown'})"
                )
            time.sleep(self.poll_interval)

    def _fetch_results(
        self, job_id: str, job: Dict[str, Any], expected_molecules: int
    ) -> List[Dict[str, Any]]:
        """Read every result page of a completed job."""

        url = self._module_url(job_id, "results")
        total_pages = job.get("num_pages_total")
        rows: List[Dict[str, Any]] = []
        page = 1

        # NERDD treats an unknown entry count as infinite and then advertises a next
        # page forever, so pagination is bounded independently of the server's hints.
        # Pages hold at least one molecule each, so the molecule count bounds the pages.
        max_pages = expected_molecules + 5
        if isinstance(total_pages, int) and total_pages > 0:
            max_pages = min(max_pages, total_pages)
        deadline = time.monotonic() + self.max_wait_seconds

        while True:
            response = self._request("GET", url, params={"page": page})

            # A completed job can briefly still report a page as incomplete (HTTP 202).
            for attempt in range(self.max_retries):
                if response.status_code != 202:
                    break
                time.sleep(self.poll_interval * (attempt + 1))
                response = self._request("GET", url, params={"page": page})

            if response.status_code == 404:
                break  # page out of range: nothing more to read
            if response.status_code >= 400 or response.status_code == 202:
                raise NerddToolError(
                    f"NERDD results page {page} of job {job_id} could not be read "
                    f"(HTTP {response.status_code})"
                )

            body = response.json()
            rows.extend(body.get("data") or [])

            pagination = body.get("pagination") or {}
            if not pagination.get("next_url"):
                break
            page += 1
            if page > max_pages:
                break
            if time.monotonic() >= deadline:
                raise NerddToolError(
                    f"NERDD results for job {job_id} were still being paginated after "
                    f"{self.max_wait_seconds:.0f}s"
                )

        return rows

    # ------------------------------------------------------- result reassembly

    @staticmethod
    def _group_rows_by_molecule(
        rows: Iterable[Dict[str, Any]],
        ordered_ids: Sequence[str],
        smiles_by_id: Dict[str, str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group result rows under the SABLE molecule id they belong to.

        NERDD numbers molecules with ``mol_id`` following the order of submission,
        which is the primary key used here. The echoed input SMILES is used as a
        fallback when ``mol_id`` is absent or out of range.
        """

        smiles_to_ids: Dict[str, List[str]] = {}
        for mol_id in ordered_ids:
            smiles_to_ids.setdefault(smiles_by_id[mol_id], []).append(mol_id)

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            mol_index = row.get("mol_id")
            molecule_id: Optional[str] = None

            if isinstance(mol_index, int) and 0 <= mol_index < len(ordered_ids):
                molecule_id = ordered_ids[mol_index]
            else:
                # input_text is the string exactly as submitted; input_smiles is
                # RDKit-canonicalized by NERDD and will usually not equal our input.
                echoed = row.get("input_text") or row.get("input_smiles")
                candidates = smiles_to_ids.get(str(echoed).strip()) if echoed else None
                if candidates:
                    molecule_id = candidates[0]

            if molecule_id is not None:
                grouped.setdefault(molecule_id, []).append(row)

        return grouped

    def _predict_batch(
        self,
        ordered_ids: Sequence[str],
        smiles_by_id: Dict[str, str],
    ) -> Dict[str, Dict[str, float]]:
        smiles_list = [smiles_by_id[mol_id] for mol_id in ordered_ids]
        job_id = self._create_job(smiles_list)
        job = self._wait_for_job(job_id)
        rows = self._fetch_results(job_id, job, expected_molecules=len(ordered_ids))
        grouped = self._group_rows_by_molecule(rows, ordered_ids, smiles_by_id)

        extractor = self.spec.extractor
        results: Dict[str, Dict[str, float]] = {}
        for molecule_id, molecule_rows in grouped.items():
            properties = extractor(molecule_rows)
            if properties:
                results[molecule_id] = properties
        return results

    def predict(self, smiles_by_id: Dict[str, str]) -> CharacterizationResult:
        """Run the module over all molecules, batching to respect NERDD job limits."""

        usable = {
            mol_id: smiles
            for mol_id, smiles in smiles_by_id.items()
            if isinstance(smiles, str) and smiles.strip()
        }
        if not usable:
            raise NerddToolError("No valid SMILES provided for NERDD characterization.")

        ordered_ids = list(usable.keys())
        batch_size = self._effective_batch_size()
        results: Dict[str, Dict[str, Any]] = {}

        for start in range(0, len(ordered_ids), batch_size):
            batch_ids = ordered_ids[start : start + batch_size]
            results.update(self._predict_batch(batch_ids, usable))

        failed = [mol_id for mol_id in smiles_by_id if mol_id not in results]
        return CharacterizationResult(
            results=results,
            failed_molecules=failed,
            metadata={
                "tool_id": f"nerdd_{self.spec.module}",
                "nerdd_module": self.spec.module,
                "nerdd_level": self.spec.level,
                "nerdd_params": self._params(),
                "molecules_requested": len(smiles_by_id),
                "molecules_returned": len(results),
            },
        )

    # ------------------------------------------------------------- entry points

    def characterize(self, request: CharacterizationRequest) -> CharacterizationResult:
        """Characterizer entry point used by ``characterize_molecules_node``."""

        smiles_by_id = {
            mol_id: request.search_space.get(mol_id)
            for mol_id in (request.molecule_ids or list(request.search_space))
        }
        smiles_by_id = {
            mol_id: smiles for mol_id, smiles in smiles_by_id.items() if smiles
        }
        return self.predict(smiles_by_id)

    def _run(
        self,
        precision: int = 2,
        search_space: Optional[Dict[str, str]] = None,
        ids_to_process: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """STOPLIGHT-style entry point returning ``{molecule_id: {property: value}}``."""

        del precision  # NERDD returns full precision values.
        search_space = search_space or {}
        ids_to_process = ids_to_process or list(search_space)
        smiles_by_id = {
            mol_id: search_space[mol_id] for mol_id in ids_to_process if search_space.get(mol_id)
        }
        return self.predict(smiles_by_id).results

    async def _arun(self, **kwargs: Any) -> str:
        raise NotImplementedError("NerddTool does not support async execution.")
