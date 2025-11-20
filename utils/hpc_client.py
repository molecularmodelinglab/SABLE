'''
    Minimal generic SSH/SFTP client for HPC interactions.
'''
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import paramiko


@dataclass
class SSHConfig:
    hostname: str
    username: str
    port: int = 22
    key_filename: Optional[str] = None  # path to the private key
    password: Optional[str] = None
    timeout: int = 10                   # in seconds


class SSHCommandError(RuntimeError):
    """Raised when a remote command exits with non-zero status."""
    def __init__(self, command: str, exit_status: int, stdout: str, stderr: str):
        super().__init__(f"Command failed with status {exit_status}: {command}")
        self.command = command
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class HPCClient:
    """
    Generic SSH/SFTP client for HPC (longleaf).

    - Reusable for any tool (boltz2, others)
    - Provides: run, put_file, get_file, put_dir, get_dir
    """
    def __init__(self, config: SSHConfig):
        self.config = config
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def connect(self) -> None:
        '''Establish SSH and SFTP connections.'''
        if self._ssh is not None:
            return

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        ssh.connect(
            hostname=self.config.hostname,
            port=self.config.port,
            username=self.config.username,
            key_filename=self.config.key_filename,
            password=self.config.password,
            timeout=self.config.timeout,
        )
        self._ssh = ssh
        self._sftp = ssh.open_sftp()

    def close(self) -> None:
        '''Close SSH and SFTP connections.'''
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._ssh is not None:
            self._ssh.close()
            self._ssh = None

    def __enter__(self) -> "HPCClient":
        '''allow usage: with HPCClient(config) as client: ...'''
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        '''cleanup on exit from with-block'''
        self.close()

    def run(
        self,
        command: str,
        check: bool = True,
        get_pty: bool = False,
    ) -> Tuple[int, str, str]:
        """
        Run a shell command on the HPC.
        
        Args:
            command: The command to execute.
            check: If True, raise SSHCommandError on non-zero exit status.
            get_pty: If True, request a pseudo-terminal from the server.
        
        Returns:
            A tuple of (exit_status, stdout, stderr). 
        
        Raises:
            SSHCommandError: If check is True and the command exits with non-zero status.
        """
        self._ensure_connected()
        assert self._ssh is not None

        stdin, stdout, stderr = self._ssh.exec_command(command, get_pty=get_pty)
        exit_status = stdout.channel.recv_exit_status()
        out_str = stdout.read().decode("utf-8", errors="ignore")
        err_str = stderr.read().decode("utf-8", errors="ignore")

        if check and exit_status != 0:
            raise SSHCommandError(command, exit_status, out_str, err_str)

        return exit_status, out_str, err_str

    def put_file(self, local_path: str | Path, remote_path: str | Path) -> None:
        """Upload a single file."""
        self._ensure_connected()
        assert self._sftp is not None

        local_path = str(local_path)
        remote_path = str(remote_path)
        self._sftp.put(local_path, remote_path)

    def get_file(self, remote_path: str | Path, local_path: str | Path) -> None:
        """Download a single file."""
        self._ensure_connected()
        assert self._sftp is not None

        remote_path = str(remote_path)
        local_path = str(local_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self._sftp.get(remote_path, local_path)

    def put_dir(self, local_dir: str | Path, remote_dir: str | Path) -> None:
        """
        Recursively upload a directory.

        Creates remote directories as needed.
        """
        self._ensure_connected()
        assert self._sftp is not None

        local_dir = Path(local_dir)
        remote_dir = str(remote_dir)

        # Ensure base remote dir exists
        self._mkdir_p(remote_dir)

        for root, dirs, files in os.walk(local_dir):
            rel_root = Path(root).relative_to(local_dir)
            remote_root = str(Path(remote_dir) / rel_root)

            self._mkdir_p(remote_root)

            for fname in files:
                local_path = Path(root) / fname
                remote_path = str(Path(remote_root) / fname)
                self._sftp.put(str(local_path), remote_path)

    def get_dir(self, remote_dir: str | Path, local_dir: str | Path) -> None:
        """
        Recursively download a directory.

        NOTE: Paramiko's SFTP doesn't have a built-in recursive get,
        so we walk manually.
        """
        self._ensure_connected()
        assert self._sftp is not None

        remote_dir = str(remote_dir)
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)

        # simple recursive walk using SFTP
        def _walk(remote_path: str, local_path: Path) -> None:
            local_path.mkdir(parents=True, exist_ok=True)
            for entry in self._sftp.listdir_attr(remote_path):
                r_name = entry.filename
                r_full = f"{remote_path.rstrip('/')}/{r_name}"
                l_full = local_path / r_name

                if paramiko.S_ISDIR(entry.st_mode):
                    _walk(r_full, l_full)
                else:
                    self._sftp.get(r_full, str(l_full))

        _walk(remote_dir, local_dir)

    # --- helpers ---

    def _ensure_connected(self) -> None:
        if self._ssh is None or self._sftp is None:
            self.connect()

    def _mkdir_p(self, remote_dir: str) -> None:
        """
        Recursively create a remote directory like `mkdir -p`.
        Ignores if parts already exist.
        """
        assert self._sftp is not None
        parts = remote_dir.strip("/").split("/")
        path = ""
        for part in parts:
            path = f"{path}/{part}" if path else f"/{part}"
            try:
                self._sftp.stat(path)
            except IOError:
                self._sftp.mkdir(path)
