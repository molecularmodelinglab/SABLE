import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from sqlalchemy.orm import Session

from server.models.provider_job import ProviderJob, ProviderJobResult
from tools.boltz.models import BoltzMetricSet, BoltzMoleculeResult, BoltzRequest, BoltzSubmission


TERMINAL_PROVIDER_STATUSES = {"succeeded", "failed", "stopped"}
ACTIVE_PROVIDER_STATUSES = {"pending", "running"}


class ProviderJobService:
    @staticmethod
    def request_fingerprint(request: BoltzRequest) -> str:
        payload = request.model_dump(mode="json", exclude={"options": {"api_key"}})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def create(
        self,
        db: Session,
        *,
        run_id: str,
        user_id: object,
        credential_id: object,
        request: BoltzRequest,
        submission: BoltzSubmission,
    ) -> ProviderJob:
        job = ProviderJob(
            run_id=run_id,
            user_id=user_id,
            credential_id=credential_id,
            provider=submission.provider.value,
            execution_kind=submission.execution_kind.value,
            provider_job_id=submission.provider_job_id,
            protein_scope_id=submission.protein_scope_id,
            request_fingerprint=self.request_fingerprint(request),
            status=submission.status.value,
            total_items=len(request.molecules),
        )
        db.add(job)
        db.flush()
        return job

    def find_for_request(
        self,
        db: Session,
        *,
        run_id: str,
        request: BoltzRequest,
    ) -> ProviderJob | None:
        return db.query(ProviderJob).filter(
            ProviderJob.run_id == run_id,
            ProviderJob.request_fingerprint == self.request_fingerprint(request),
        ).first()

    @staticmethod
    def submission_for(job: ProviderJob, request: BoltzRequest) -> BoltzSubmission:
        return BoltzSubmission(
            provider=job.provider,
            execution_kind=job.execution_kind,
            provider_job_id=job.provider_job_id,
            protein_scope_id=job.protein_scope_id,
            status=job.status,
            metadata={
                "molecule_ids_by_smiles": {
                    molecule.smiles: molecule.id for molecule in request.molecules
                },
            },
        )

    def update_status(
        self,
        db: Session,
        job: ProviderJob,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ProviderJob:
        now = datetime.now(timezone.utc)
        job.status = status
        job.last_polled_at = now
        job.error_code = error_code
        job.error_message = error_message
        if status in TERMINAL_PROVIDER_STATUSES:
            job.completed_at = now
        db.flush()
        return job

    def store_results(
        self,
        db: Session,
        job: ProviderJob,
        results: Iterable[BoltzMoleculeResult],
    ) -> None:
        for result in results:
            record = db.query(ProviderJobResult).filter(
                ProviderJobResult.provider_job_id == job.id,
                ProviderJobResult.molecule_id == result.molecule_id,
            ).first()
            if record is None:
                record = ProviderJobResult(
                    provider_job_id=job.id,
                    molecule_id=result.molecule_id,
                )
                db.add(record)
            record.provider_result_id = result.provider_result_id
            record.status = result.status.value
            record.metrics = result.metrics.model_dump(mode="json", exclude_none=True)
            if result.structure_path and not result.structure_path.startswith(("http://", "https://")):
                record.artifact_path = result.structure_path
            record.warnings = list(result.warnings)
            record.error_message = result.sanitized_error

        db.flush()
        rows = db.query(ProviderJobResult).filter(
            ProviderJobResult.provider_job_id == job.id,
        ).all()
        job.completed_items = sum(row.status == "succeeded" for row in rows)
        job.failed_items = sum(row.status == "failed" for row in rows)
        db.flush()

    @staticmethod
    def load_results(job: ProviderJob) -> list[BoltzMoleculeResult]:
        return [
            BoltzMoleculeResult(
                molecule_id=row.molecule_id,
                protein_scope_id=job.protein_scope_id,
                provider_result_id=row.provider_result_id,
                status=row.status,
                metrics=BoltzMetricSet.model_validate(row.metrics or {}),
                structure_path=row.artifact_path,
                warnings=list(row.warnings or []),
                sanitized_error=row.error_message,
            )
            for row in job.results
        ]

    def has_active_jobs(self, db: Session, credential_id: object) -> bool:
        return db.query(ProviderJob.id).filter(
            ProviderJob.credential_id == credential_id,
            ProviderJob.status.in_(ACTIVE_PROVIDER_STATUSES),
        ).first() is not None


provider_job_service = ProviderJobService()