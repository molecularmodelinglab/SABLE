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
        mock_poll = MagicMock()
        
        mock_slurm.submit_boltz_job = mock_submit
        mock_slurm.poll_boltz_job = mock_poll

        mock_submit_task = MagicMock()
        mock_submit_task.get.return_value = {"job_id": "job_123"}
        mock_submit.delay.return_value = mock_submit_task

        mock_poll_task = MagicMock()
        mock_poll_task.get.return_value = {
            "status": "completed", 
            "outputs_dir": "/tmp/mock_outputs",
            "job_id": "job_123"
        }
        mock_poll.delay.return_value = mock_poll_task

        with patch.dict(sys.modules, {"server.tasks.slurm": mock_slurm}):
            with patch("pathlib.Path.glob") as mock_glob, \
                 patch("builtins.open", unittest.mock.mock_open(read_data='{"affinity": -9.5}')) as mock_file:
                
                mock_glob.side_effect = [
                    [MagicMock()], # affinity
                    [], # confidence
                    []  # cif
                ]
                
                result_json = self.tool._run(
                    ligands={"lig1": "CCO"},
                    polymers=[{"chain_id": "A", "sequence": "AAAA"}]
                )

        result = json.loads(result_json)

        self.assertEqual(result["count"], 1)
        self.assertIn("lig1", result["per_ligand"])
        self.assertEqual(result["per_ligand"]["lig1"]["job_id"], "job_123")
        self.assertEqual(result["per_ligand"]["lig1"]["affinity"], {"affinity": -9.5})
        
        mock_submit.delay.assert_called_once()

        mock_poll.delay.assert_called()

if __name__ == "__main__":
    unittest.main()
