"""Owned-process and real TCP fault injection helpers for isolated acceptance."""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def process_table():
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            result[int(path.parent.name)] = (int(fields[1]), fields[19], fields[0])
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


class Processes:
    def __init__(self, broker_url):
        # Reap orphaned prefork children after terminating their owned worker parent.
        assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
        self.env = {**os.environ, "CELERY_BROKER_URL": broker_url}
        self.roots = []
        self.logs = []
        self.owned = {}
        self.killed = []

    def spawn(self, role, value):
        log = Path("/results") / f"phase6-{role}-{value}.log"
        handle = log.open("w", encoding="utf-8")
        self.logs.append(handle)
        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("multiprocess_runtime.py")),
                                  role, str(value)], env=self.env, stdout=handle,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        self.roots.append(child)
        self.capture(child.pid)
        return child

    def capture(self, root):
        table = process_table()
        selected = {root} if root in table else set()
        while True:
            more = {pid for pid, row in table.items() if row[0] in selected}
            if more <= selected:
                break
            selected |= more
        for pid in selected:
            self.owned[pid] = table[pid][1]
        return selected

    def send(self, pid, sig):
        row = process_table().get(pid)
        if row and row[1] == self.owned.get(pid) and row[2] != "Z":
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def terminate_worker(self, child):
        self.send(child.pid, signal.SIGSTOP)
        pids = self.capture(child.pid)
        for pid in pids - {child.pid}:
            self.send(pid, signal.SIGKILL)
        self.send(child.pid, signal.SIGKILL)
        child.wait(timeout=5)
        self.killed.extend(sorted(pids))
        return pids

    async def close(self):
        for child in self.roots:
            self.capture(child.pid)
            self.send(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while any(p.poll() is None for p in self.roots) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        for child in self.roots:
            self.capture(child.pid)
        for pid in list(self.owned):
            self.send(pid, signal.SIGKILL)
        for child in self.roots:
            child.wait(timeout=5)
        # SIGKILL delivery is asynchronous; reap until every owned child exits.
        deadline = time.monotonic() + 5
        while True:
            for pid in self.owned:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            remaining = {pid: row for pid, row in process_table().items()
                         if pid in self.owned and row[1] == self.owned[pid] and row[2] != "Z"}
            if not remaining or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        for handle in self.logs:
            handle.close()
        Path("/results/phase6-cleanup.json").write_text(json.dumps({
            "owned_pids": sorted(self.owned), "fault_killed_pids": self.killed,
            "remaining_live_owned_pids": remaining,
        }, indent=2), encoding="utf-8")
        assert not remaining, f"Owned processes survived cleanup: {remaining}"


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BrokerProxy:
    """Close real established transport sockets and refuse new ones during outage."""
    def __init__(self):
        self.up = True
        self.writers = set()
        self.handlers = set()
        self.established = 0
        self.dropped = 0
        self.rejected = 0

    async def start(self):
        self.server = await asyncio.start_server(self.accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return f"redis://127.0.0.1:{self.port}/1"

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        upstream = None
        pumps = []
        try:
            if not self.up:
                self.rejected += 1
                return
            remote_reader, upstream = await asyncio.wait_for(
                asyncio.open_connection("redis", 6379), 2)
            if not self.up:
                self.rejected += 1
                return
            self.established += 1
            self.writers.update((writer, upstream))

            async def copy(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()

            pumps = [asyncio.create_task(copy(reader, upstream)),
                     asyncio.create_task(copy(remote_reader, writer))]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, upstream):
                if stream:
                    self.writers.discard(stream)
                    stream.close()
            self.handlers.discard(task)

    def disconnect(self):
        self.up = False
        self.dropped += len(self.writers) // 2
        for writer in tuple(self.writers):
            writer.transport.abort()

    async def close(self):
        self.disconnect()
        self.server.close()
        await self.server.wait_closed()
        for task in tuple(self.handlers):
            task.cancel()
        await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
