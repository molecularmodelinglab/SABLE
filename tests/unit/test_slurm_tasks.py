from unittest.mock import MagicMock, patch

from server.tasks.slurm import get_job_manager, monitor_slurm_jobs


def test_monitor_skips_ssh_manager_in_api_mode(monkeypatch):
    monkeypatch.setenv("BOLTZ_EXECUTION_MODE", "api")

    with patch(
        "server.tasks.slurm.get_job_manager",
        side_effect=AssertionError("SSH manager should not be initialized"),
    ):
        assert monitor_slurm_jobs.run() is None


def test_job_manager_does_not_require_ssh_key(monkeypatch):
    monkeypatch.setenv("HPC_HOST", "hpc.example.com")
    monkeypatch.delenv("HPC_SSH_KEY", raising=False)
    monkeypatch.delenv("HPC_SSH_KEY_CONTENT", raising=False)
    monkeypatch.delenv("HPC_PASSWORD", raising=False)

    manager = MagicMock()
    with (
        patch("server.tasks.slurm.os.path.isfile", return_value=False),
        patch("server.tasks.slurm.BoltzJobManager", return_value=manager) as manager_class,
    ):
        assert get_job_manager() is manager

    ssh_config = manager_class.call_args.kwargs["hpc"].config
    assert ssh_config.key_filename is None
    assert ssh_config.password is None