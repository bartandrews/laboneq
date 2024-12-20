# Copyright 2024 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

"""Implementation of logbook which stores data in a folder."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from laboneq.workflow.recorder import Artifact
from laboneq.workflow.timestamps import local_date_stamp, local_timestamp, utc_now
from . import Logbook, LogbookStore
from .deduplication import DeduplicationCache
from .serializer import SerializeOpener
from .serializer import (
    serialize as default_serialize,
)
from .simple_serializer import simple_serialize, NOT_SIMPLE

if TYPE_CHECKING:
    import datetime
    from typing import IO, Callable

    from laboneq.workflow import Workflow, WorkflowResult
    from laboneq.workflow.typing import SimpleDict
    from laboneq.workflow.result import TaskResult


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename to make it Windows compatible."""
    # TODO: Make this more like slugify and contract multiple - into one.
    return (
        filename.replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
        .replace("_", "-")
    )


class FolderStore(LogbookStore):
    """A folder-based store that stores workflow results and artifacts in a folder.

    The store produces an log file `.jsonl`, and in some cases external files.
    An external file is generated when an object to be saved is too large to
    fit nicely into the log file, and when it happens, the logfile will have a
    reference pointing into the file the data was saved to.

    For tasks which are marked as not to be saved, their inputs and outputs are
    omitted from logfiles and from generating any external files.
    """

    def __init__(self, folder: Path | str, serialize: Callable | None = None):
        self._folder = Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)
        if serialize is None:
            serialize = default_serialize
        self._serialize = serialize

    def create_logbook(
        self, workflow: Workflow, start_time: datetime.datetime
    ) -> Logbook:
        """Create a new logbook for the given workflow."""
        day = local_date_stamp(start_time)
        Path(self._folder / day).mkdir(parents=False, exist_ok=True)
        assert workflow.name is not None  # noqa: S101
        folder_name = self._unique_workflow_folder_name(workflow.name, start_time)

        return FolderLogbook(self._folder / day / folder_name, self._serialize)

    def _unique_workflow_folder_name(
        self, workflow_name: str, start_time: datetime.datetime
    ) -> str:
        """Generate a unique workflow folder name within the storage folder.

        Arguments:
            workflow_name: The name of the workflow.
            start_time: The start time of the workflow execution.

        Returns:
            A unique name for the folder.
        """
        day = local_date_stamp(start_time)
        ts = local_timestamp(start_time)
        workflow_name = _sanitize_filename(workflow_name)
        count = 0
        while True:
            if count > 0:
                potential_name = f"{ts}-{workflow_name}-{count}"
            else:
                potential_name = f"{ts}-{workflow_name}"
            workflow_path = self._folder / day / potential_name
            if not workflow_path.exists():
                return potential_name
            count += 1


class FolderLogbookOpener(SerializeOpener):
    """A serialization file opener for the FolderStore and FolderLogbook.

    Files are opened in the logbook folder with the base filename being
    `{artifact_name}-{suffix}.{ext}`. The dash is omitted if the suffix
    is empty.

    Arguments:
        logbook:
            The logbook files will be opened for.
        artifact_name:
            The name of the artifact being serialized.
        serializer_options:
            The options for the serializer.
    """

    def __init__(
        self,
        logbook: FolderLogbook,
        artifact_name: str,
        serializer_options: SimpleDict,
    ):
        self._logbook = logbook
        self._artifact_name = artifact_name
        self._serializer_options = serializer_options
        self._artifact_files = ArtifactFiles()

    def open(
        self,
        ext: str,
        *,
        encoding: str | None = None,
        suffix: str | None = None,
        description: str | None = None,
        binary: bool = False,
    ) -> IO:
        """Open the requested file in the logbook folder."""
        mode = "wb" if binary else "w"
        suffix = f"-{suffix}" if suffix else ""
        filename = self._logbook._unique_filename(
            f"{self._artifact_name}{suffix}.{ext}",
        )
        path = self._logbook._folder / filename
        self._artifact_files.add(filename, description)
        return path.open(mode=mode, encoding=encoding)

    def options(self) -> dict:
        """Return the serializer options."""
        return self._serializer_options

    def name(self) -> str:
        """Return a name for the object being serialized."""
        return self._artifact_name

    def artifact_files(self) -> ArtifactFiles:
        """Return the list of files that were opened."""
        return self._artifact_files


class ArtifactFiles:
    """A record of files opened for an artifact."""

    def __init__(self):
        self._files = []

    def add(self, filename: str, description: str | None = None) -> None:
        """Add the specified file to the list of opened files."""
        entry = {
            "filename": filename,
        }
        if description is not None:
            entry["description"] = description
        self._files.append(entry)

    def as_dicts(self) -> list[dict[str, str]]:
        """Return a list of dictionaries describing the opened files.

        The returned dictionaries consist of:
        ```python
        {
            "filename": <filename>,
            "description": <description>,
        }
        ```

        The `description` is omitted if none was supplied by the
        serializer.
        """
        return self._files


class FolderLogbook(Logbook):
    """A logbook that stores a workflow's results and artifacts in a folder."""

    def __init__(
        self,
        folder: Path | str,
        serialize: Callable[[object, FolderLogbookOpener], None],
    ) -> None:
        self._folder = Path(folder)
        self._folder.mkdir(parents=False, exist_ok=False)
        self._log = Path(self._folder / "log.jsonl")
        self._log.touch(exist_ok=False)
        self._serialize = serialize
        self._deduplication_cache: DeduplicationCache = DeduplicationCache()

    def _unique_filename(
        self,
        filename: str,
    ) -> str:
        """Generate a unique filename within the workflow folder.

        Arguments:
            filename: The name of the file with its extension.

        Returns:
            A unique name for the file.
        """
        filename = _sanitize_filename(filename)
        filepath = Path(filename)
        stem, suffix = filepath.stem, filepath.suffix

        count = 0
        while True:
            if count > 0:
                potential_name = f"{stem}-{count}{suffix}"
            else:
                potential_name = f"{stem}{suffix}"
            file_path = self._folder / potential_name
            if not file_path.exists():
                return potential_name
            count += 1

    def _append_log(self, data: dict[str, object]) -> None:
        with self._log.open(mode="a", encoding="utf-8") as f:
            json.dump(data, f)
            f.write("\n")

    def _save(self, artifact: Artifact, *, deduplicate=True) -> ArtifactFiles:
        """Store an artifact in one or more files."""
        opts = artifact.options.copy()
        files = (
            cast(
                ArtifactFiles,
                self._deduplication_cache.get_from_object(
                    artifact.obj, artifact.options
                ),
            )
            if deduplicate
            else None
        )
        if files is None:
            opener = FolderLogbookOpener(self, artifact.name, opts)
            self._serialize(artifact.obj, opener)
            files = opener.artifact_files()
            self._deduplication_cache.store_object(
                artifact.obj, artifact.options, files
            )
        return files

    def _save_input(
        self,
        inpt: dict[str, object],
        name_hint: str,
    ) -> dict:
        """Store artifacts for task or workflow inputs."""
        input_dict = {}
        for k, v in inpt.items():
            simple = simple_serialize(v)
            if simple is not NOT_SIMPLE:
                input_dict[k] = simple
            else:
                artifact = Artifact(f"{name_hint}.{k}", v)
                try:
                    ref = self._save(artifact)
                except TypeError:
                    # TODO: Less silent errors
                    input_dict[k] = "..."
                else:
                    input_dict[k] = ref.as_dicts()
        return input_dict

    def _save_output(self, result: object, name_hint: str) -> dict:
        """Store artifacts for task or workflow results."""
        simple = simple_serialize(result)
        if simple is not NOT_SIMPLE:
            return simple

        if not isinstance(result, dict):
            artifact = Artifact(f"{name_hint}", result)
            try:
                ref = self._save(artifact)
            except TypeError:
                # TODO: Less silent errors
                return "..."
            else:
                return ref.as_dicts()

        result_dict = {}
        for k, v in result.items():
            simple = simple_serialize(v)
            if simple is not NOT_SIMPLE:
                result_dict[k] = simple
            else:
                artifact = Artifact(f"{name_hint}.{k}", v)
                try:
                    ref = self._save(artifact)
                except TypeError:
                    # TODO: Less silent errors
                    result_dict[k] = "..."
                else:
                    result_dict[k] = ref.as_dicts()
        return result_dict

    def on_start(self, workflow_result: WorkflowResult) -> None:
        """Called when the workflow execution starts."""
        self._append_log(
            {
                "event": "start",
                "workflow": workflow_result.name,
                "time": str(utc_now(workflow_result.start_time)),
                "input": self._save_input(
                    workflow_result.input,
                    name_hint=f"{workflow_result.name}.input",
                ),
            },
        )

    def on_end(self, workflow_result: WorkflowResult) -> None:
        """Called when the workflow execution ends."""
        self._append_log(
            {
                "event": "end",
                "workflow": workflow_result.name,
                "time": str(utc_now(workflow_result.end_time)),
                "output": self._save_output(
                    workflow_result.output, name_hint=f"{workflow_result.name}.output"
                ),
            },
        )

    def on_error(
        self,
        workflow_result: WorkflowResult,
        error: Exception,
    ) -> None:
        """Called when the workflow raises an exception."""
        self._append_log(
            {
                "event": "error",
                "workflow": workflow_result.name,
                "error": repr(error),
                "time": str(utc_now(workflow_result.end_time)),
            },
        )

    def on_task_start(
        self,
        task: TaskResult,
    ) -> None:
        """Called when a task begins execution."""
        entry = {
            "event": "task_start",
            "task": task.name,
            "time": str(utc_now(task.start_time)),
        }
        if task.task.save:
            entry["input"] = self._save_input(
                task.input,
                name_hint=f"{task.name}.input",
            )
        self._append_log(entry)

    def on_task_end(
        self,
        task: TaskResult,
    ) -> None:
        """Called when a task ends execution."""
        entry = {
            "event": "task_end",
            "task": task.name,
            "time": str(utc_now(task.end_time)),
        }
        if task.task.save:
            entry["output"] = self._save_output(
                task.output,
                name_hint=f"{task.name}.output",
            )
        self._append_log(entry)

    def on_task_error(
        self,
        task: TaskResult,
        error: Exception,
    ) -> None:
        """Called when a task raises an exception."""
        self._append_log(
            {
                "event": "task_error",
                "task": task.name,
                "error": repr(error),
                "time": str(utc_now(task.end_time)),
            },
        )

    def comment(self, message: str) -> None:
        """Called to leave a comment."""
        self._append_log(
            {
                "event": "comment",
                "message": message,
                "time": str(utc_now()),
            },
        )

    def log(self, level: int, message: str, *args: object) -> None:
        """Called to leave a log message."""
        self._append_log(
            {
                "event": "log",
                "message": message % args,
                "time": str(utc_now()),
                "level": level,
            },
        )

    def save(
        self,
        artifact: Artifact,
    ) -> None:
        """Called to save an artifact."""
        ref = self._save(artifact, deduplicate=False)
        self._append_log(
            {
                "event": "artifact",
                "time": str(utc_now(artifact.timestamp)),
                "artifact_name": artifact.name,
                "artifact_type": type(artifact.obj).__name__,
                "artifact_metadata": artifact.metadata,
                "artifact_options": artifact.options,
                "artifact_files": ref.as_dicts(),
            },
        )
