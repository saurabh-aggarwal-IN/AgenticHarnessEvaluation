from __future__ import annotations

from pathlib import Path

import pytest

from harness_compare.config import Config
from harness_compare.docker_runner import DockerClient, DockerError
from harness_compare.process import CommandResult


class RecordingRunner:
    def __init__(self, *, fail_first: bool = False, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.fail_first = fail_first
        self.failed = False
        self.stderr = stderr

    async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
        self.calls.append(list(args))
        if self.fail_first and not self.failed:
            self.failed = True
            return CommandResult(args, 1, "", self.stderr or "boom", 1)
        return CommandResult(args, 0, "container-id\n", "", 1)


@pytest.mark.asyncio
async def test_start_worker_hardens_container(cfg: Config, tmp_path: Path) -> None:
    runner = RecordingRunner()
    docker = DockerClient(cfg, runner)
    await docker.start_worker(
        name="cc-cmp-h1",
        image="clinical-coder-h1:test",
        network="cc-cmp-test",
        comparison_id="cmp-test",
        harness_id="h1",
        env={"HARNESS_ID": "h1", "LLM_API_KEY": "secret"},
        env_file=tmp_path / "h1.env",
    )
    args = runner.calls[0]
    for required in (
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--memory",
        "--cpus",
        "--pids-limit",
        "--read-only",
        "--init",
        "--env-file",
    ):
        assert required in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[-3:] == [
        "/opt/harness-venv/bin/python",
        "-m",
        "clinical_coder.worker",
    ]
    env_text = (tmp_path / "h1.env").read_text(encoding="utf-8")
    assert "HARNESS_ID=h1" in env_text
    assert "LLM_API_KEY=secret" in env_text


@pytest.mark.asyncio
async def test_start_worker_can_disable_cgroup_ceilings(
    cfg: Config, tmp_path: Path
) -> None:
    from dataclasses import replace

    docker = DockerClient(
        replace(cfg, worker_memory="", worker_cpus="", worker_pids=0),
        RecordingRunner(),
    )
    await docker.start_worker(
        name="cc-cmp-h1",
        image="image",
        network="net",
        comparison_id="cmp",
        harness_id="h1",
        env={"HARNESS_ID": "h1"},
        env_file=tmp_path / "h1.env",
    )
    args = docker.runner.calls[0]
    assert "--memory" not in args
    assert "--cpus" not in args
    assert "--pids-limit" not in args
    assert "--cap-drop" in args
    assert "--read-only" in args


@pytest.mark.asyncio
async def test_start_worker_retries_with_explicit_env(
    cfg: Config, tmp_path: Path
) -> None:
    runner = RecordingRunner(fail_first=True)
    docker = DockerClient(cfg, runner)
    await docker.start_worker(
        name="cc-cmp-h2",
        image="clinical-coder-h2:test",
        network="cc-cmp-test",
        comparison_id="cmp-test",
        harness_id="h2",
        env={"HARNESS_ID": "h2", "LLM_API_KEY": "secret"},
        env_file=tmp_path / "h2.env",
    )
    assert len(runner.calls) == 2
    retry = runner.calls[1]
    assert "--env-file" not in retry
    assert "-e" in retry
    assert "HARNESS_ID=h2" in retry
    assert "LLM_API_KEY=secret" in retry
    assert retry.index("clinical-coder-h2:test") > retry.index("-e")


@pytest.mark.asyncio
async def test_start_worker_rejects_newline_in_env(cfg: Config, tmp_path: Path) -> None:
    docker = DockerClient(cfg, RecordingRunner())
    with pytest.raises(DockerError) as exc:
        await docker.start_worker(
            name="cc-cmp-h1",
            image="image",
            network="net",
            comparison_id="cmp",
            harness_id="h1",
            env={"EVIL": "a\nb"},
            env_file=tmp_path / "x.env",
        )
    assert exc.value.code == "DOCKER_ENV_INVALID"


@pytest.mark.asyncio
async def test_build_image_writes_log_and_preserves_error(
    cfg: Config, tmp_path: Path
) -> None:
    docker = DockerClient(
        cfg, RecordingRunner(fail_first=True, stderr="step 4 failed")
    )
    log = tmp_path / "build.log"
    with pytest.raises(DockerError) as exc:
        await docker.build_image(
            context=tmp_path,
            tag="clinical-coder-h1:test",
            harness_id="h1",
            commit="a" * 40,
            comparison_id="cmp",
            version="1.0.0",
            log_path=log,
        )
    assert exc.value.code == "DOCKER_COMMAND_FAILED"
    assert log.is_file()
    assert "step 4 failed" in log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_build_image_uses_build_args(cfg: Config, tmp_path: Path) -> None:
    runner = RecordingRunner()
    docker = DockerClient(cfg, runner)
    await docker.build_image(
        context=tmp_path,
        tag="clinical-coder-h3:test",
        harness_id="h3",
        commit="b" * 40,
        comparison_id="cmp",
        version="1.0.0",
        log_path=tmp_path / "build.log",
    )
    args = runner.calls[0]
    assert args[0] == cfg.docker_bin
    assert args[1] == "build"
    assert args[args.index("--build-arg") + 1] == "HARNESS_ID=h3"
    assert "GIT_COMMIT=" + "b" * 40 in args
    assert args[-1] == str(tmp_path)


@pytest.mark.asyncio
async def test_network_connect_tolerates_already_connected(
    cfg: Config,
) -> None:
    class AlreadyConnected(RecordingRunner):
        async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
            self.calls.append(list(args))
            return CommandResult(args, 1, "", "endpoint already exists", 1)

    docker = DockerClient(cfg, AlreadyConnected())
    await docker.connect_container("cc-cmp-test", "mock-llm")


@pytest.mark.asyncio
async def test_list_comparison_containers_uses_label_filter(
    cfg: Config,
) -> None:
    class Names(RecordingRunner):
        async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
            self.calls.append(list(args))
            return CommandResult(args, 0, "cc-cmp-h1\ncc-cmp-h2\n", "", 1)

    docker = DockerClient(cfg, Names())
    names = await docker.list_comparison_containers("cmp-test")
    assert names == ["cc-cmp-h1", "cc-cmp-h2"]
    assert "label=cc.comparison=cmp-test" in docker.runner.calls[0]


@pytest.mark.asyncio
async def test_worker_status_parses_json(cfg: Config) -> None:
    class State(RecordingRunner):
        async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
            self.calls.append(list(args))
            return CommandResult(args, 0, '{"Running": false, "ExitCode": 1}', "", 1)

    docker = DockerClient(cfg, State())
    status = await docker.worker_status("cc-cmp-h1")
    assert status == {"Running": False, "ExitCode": 1}
