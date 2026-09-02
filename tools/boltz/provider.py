from typing import List, Protocol

from tools.boltz.models import (
    BoltzJobStatus,
    BoltzMoleculeResult,
    BoltzRequest,
    BoltzSubmission,
)


class BoltzProviderAdapter(Protocol):
    async def submit(self, request: BoltzRequest) -> BoltzSubmission: ...

    async def poll(self, submission: BoltzSubmission) -> BoltzJobStatus: ...

    async def collect_results(
        self,
        submission: BoltzSubmission,
    ) -> List[BoltzMoleculeResult]: ...

    async def cancel(self, submission: BoltzSubmission) -> None: ...