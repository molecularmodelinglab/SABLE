import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.boltz_tool import BoltzTool, BoltzInput

class TestAsyncRuns(unittest.TestCase):
    
    def setUp(self):
        self.mock_celery_submit = MagicMock()
        self.mock_celery_poll = MagicMock()
        
        self.modules_patcher = patch.dict('sys.modules', {
            'server.tasks.slurm': MagicMock(
                submit_boltz_job=self.mock_celery_submit,
                poll_boltz_job=self.mock_celery_poll
            )
        })
        self.modules_patcher.start()
        
    def tearDown(self):
        self.modules_patcher.stop()
        
    def test_boltz_tool_uses_celery(self):
        """Test that BoltzTool uses Celery tasks when available."""
        
        mock_submit_task = MagicMock()
        mock_submit_task.get.return_value = {"job_id": "test_job_123"}
        self.mock_celery_submit.delay.return_value = mock_submit_task
        
        mock_poll_task = MagicMock()
        mock_poll_task.get.side_effect = [
            {"status": "running", "job_id": "test_job_123"},
            {"status": "completed", "job_id": "test_job_123", "outputs_dir": "/tmp/test_outputs"}
        ]
        self.mock_celery_poll.delay.return_value = mock_poll_task
        
        tool = BoltzTool(
            base_url="http://mock",
            api_token="mock",
            poll_interval=0.1,
            poll_attempts=5
        )
        
        result_json = tool._run(
            ligands={"lig1": "CCO"},
            polymers=[{"chain_id": "A", "sequence": "AAAA"}]
        )
        
        self.mock_celery_submit.delay.assert_called_once()
        
        self.assertTrue(self.mock_celery_poll.delay.call_count >= 2)
        
        result = json.loads(result_json)
        self.assertIn("lig1", result["per_ligand"])
        self.assertEqual(result["per_ligand"]["lig1"]["job_id"], "test_job_123")
        self.assertEqual(result["per_ligand"]["lig1"]["status"], "completed")

    def test_boltz_tool_fallback_http(self):
        """Test that BoltzTool falls back to HTTP if Celery import fails."""
        
        # Unpatch sys.modules to simulate import error (or patch with side_effect)
        self.modules_patcher.stop()
        
        with patch('tools.boltz_tool.requests.Session') as mock_session_cls:
            mock_session = mock_session_cls.return_value
            
            mock_session.post.return_value.status_code = 200
            mock_session.post.return_value.json.return_value = {
                "job_id": "http_job_123",
                "status": "completed",
                "affinity": -5.0,
                "confidence": 0.9
            }
            
            tool = BoltzTool(base_url="http://mock", api_token="mock")
            
            if 'server.tasks.slurm' in sys.modules:
                del sys.modules['server.tasks.slurm']
            
            with patch.dict('sys.modules', {'server.tasks.slurm': None}):
                 # When sys.modules has None, import raises ImportError
                 
                 result_json = tool._run(
                    ligands={"lig1": "CCO"},
                    polymers=[{"chain_id": "A", "sequence": "AAAA"}]
                )
                 
            mock_session.post.assert_called()
            
            result = json.loads(result_json)
            self.assertEqual(result["per_ligand"]["lig1"]["job_id"], "http_job_123")

if __name__ == '__main__':
    unittest.main()
