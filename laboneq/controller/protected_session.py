# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from laboneq.controller.util import LabOneQControllerException, SimpleProxy
from laboneq.data.experiment_results import ExperimentResults
from laboneq.dsl.result.results import Results


class ProtectedSession(SimpleProxy):
    def __init__(self, wrapped_session: Any, experiment_results: ExperimentResults):
        super().__init__(wrapped_session)
        self._experiment_results = experiment_results

    # Backwards compatibility after migration to the new architecture
    @property
    def results(self) -> Results:
        return self._last_results

    # Backwards compatibility after migration to the new architecture
    @property
    def _last_results(self) -> Results:

        return Results(
            acquired_results=self._experiment_results.acquired_results,
            user_func_results=self._experiment_results.user_func_results,
            execution_errors=self._experiment_results.execution_errors,
        )

    def disconnect(self):
        raise LabOneQControllerException(
            "'disconnect' is not allowed from the user function."
        )
