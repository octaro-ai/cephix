from __future__ import annotations

import io
import logging
import tarfile
from typing import Any

from src.workstation.ports import ExecResult

logger = logging.getLogger(__name__)


class DockerWorkstationBackend:
    """WorkstationPort implementation backed by a local Docker container.

    Uses the ``docker`` Python SDK (docker-py) to manage a named container
    with a persistent volume mounted at ``/workspace``.
    """

    def __init__(
        self,
        *,
        image: str = "ubuntu:24.04",
        volume: str = "cephix-workspace",
        container_name: str | None = None,
        memory_limit: str = "2g",
        cpu_count: int = 2,
    ) -> None:
        self._image = image
        self._volume = volume
        self._container_name = container_name or f"cephix-ws-{volume}"
        self._memory_limit = memory_limit
        self._cpu_count = cpu_count
        self._container: Any = None
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import docker
            except ImportError:
                raise RuntimeError(
                    "Docker SDK not installed. Run: pip install docker"
                )
            self._client = docker.from_env()
        return self._client

    def start(self) -> dict[str, Any]:
        client = self._get_client()
        try:
            self._container = client.containers.get(self._container_name)
            if self._container.status != "running":
                self._container.start()
                logger.info("Resumed workstation container %s", self._container_name)
            else:
                logger.info("Workstation container %s already running", self._container_name)
        except Exception:
            # Container doesn't exist yet — create it.
            self._container = client.containers.run(
                self._image,
                name=self._container_name,
                volumes={self._volume: {"bind": "/workspace", "mode": "rw"}},
                detach=True,
                tty=True,
                stdin_open=True,
                mem_limit=self._memory_limit,
                nano_cpus=self._cpu_count * 1_000_000_000,
                working_dir="/workspace",
            )
            logger.info("Created workstation container %s from %s", self._container_name, self._image)

        return self.status()

    def stop(self) -> None:
        if self._container is not None:
            self._container.stop()
            logger.info("Stopped workstation container %s", self._container_name)

    def status(self) -> dict[str, Any]:
        # Try to find the container even if we haven't started it this session.
        if self._container is None:
            try:
                client = self._get_client()
                self._container = client.containers.get(self._container_name)
            except Exception:
                return {
                    "running": False,
                    "container": self._container_name,
                    "image": self._image,
                    "volume": self._volume,
                    "workspace": "/workspace",
                }

        self._container.reload()
        return {
            "running": self._container.status == "running",
            "container": self._container_name,
            "image": self._image,
            "volume": self._volume,
            "workspace": "/workspace",
            "memory_limit": self._memory_limit,
            "cpu_count": self._cpu_count,
        }

    def exec(self, command: str, *, timeout: int = 30) -> ExecResult:
        if self._container is None or self._container.status != "running":
            raise RuntimeError("Workstation is not running. Call workstation.start first.")

        exit_code, output = self._container.exec_run(
            ["bash", "-c", command],
            workdir="/workspace",
            demux=True,
        )
        stdout = (output[0] or b"").decode("utf-8", errors="replace") if output else ""
        stderr = (output[1] or b"").decode("utf-8", errors="replace") if output else ""
        return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    def put_file(self, path: str, content: bytes) -> None:
        if self._container is None or self._container.status != "running":
            raise RuntimeError("Workstation is not running. Call workstation.start first.")

        # Docker SDK expects a tar archive for put_archive.
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            info = tarfile.TarInfo(name=path.split("/")[-1])
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_buf.seek(0)

        # Extract directory from path for the destination.
        dest_dir = "/".join(path.split("/")[:-1]) or "/workspace"
        self._container.put_archive(dest_dir, tar_buf)

    def get_file(self, path: str) -> bytes:
        if self._container is None or self._container.status != "running":
            raise RuntimeError("Workstation is not running. Call workstation.start first.")

        bits, _ = self._container.get_archive(path)
        buf = io.BytesIO()
        for chunk in bits:
            buf.write(chunk)
        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            if f is None:
                raise FileNotFoundError(f"Cannot read {path} (is it a directory?)")
            return f.read()
