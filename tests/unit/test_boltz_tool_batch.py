import unittest
from unittest.mock import MagicMock, patch
import json
import os
import sys
from tools.boltz_tool import BoltzTool

class TestBoltzToolBatch(unittest.TestCase):
    def setUp(self):
        self.tool = BoltzTool(
            base_url="http://mock-boltz",
            api_token="mock-token",
            cif_save_dir="/tmp/mock-cifs",
            fetch_cif=True
        )

    def test_run_batch_submission(self):

        mock_slurm = MagicMock()
        
        mock_submit = MagicMock()
        # poll is not used in async mode
        
        mock_slurm.submit_boltz_job = mock_submit

        mock_submit_task = MagicMock()
        mock_submit_task.id = "celery_task_123"
        mock_submit.delay.return_value = mock_submit_task

        with patch.dict(sys.modules, {"server.tasks.slurm": mock_slurm}):
            result_json = self.tool._run(
                ligands={"lig1": "CCO"},
                polymers=[{"chain_id": "A", "sequence": "AAAA"}]
            )

        result = json.loads(result_json)

        self.assertEqual(result["count"], 1)
        entry = result["per_ligand"]["lig1"]
        self.assertEqual(entry["status"], "submitted")
        self.assertEqual(entry["celery_task_id"], "celery_task_123")
        self.assertIn("job_id", entry)
        self.assertIn("message", entry)
        
        mock_submit.delay.assert_called_once()
        
        # Verify job_id was passed to delay
        _, kwargs = mock_submit.delay.call_args
        self.assertIn("job_id", kwargs)
        self.assertEqual(kwargs["job_id"], entry["job_id"])

if __name__ == "__main__":
    unittest.main()
