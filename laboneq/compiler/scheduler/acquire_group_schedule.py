# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from attrs import define

from laboneq.compiler.common.compiler_settings import CompilerSettings
from laboneq.compiler.common.event_type import EventType
from laboneq.compiler.common.pulse_parameters import encode_pulse_parameters
from laboneq.compiler.scheduler.interval_schedule import IntervalSchedule
from laboneq.data.compilation_job import ParameterInfo, SectionSignalPulse


@define(kw_only=True, slots=True)
class AcquireGroupSchedule(IntervalSchedule):
    pulses: list[SectionSignalPulse]
    amplitudes: list[float]
    phases: list[float]
    offset: int
    oscillator_frequencies: list[Optional[float]]
    set_oscillator_phases: list[Optional[float]]
    increment_oscillator_phases: list[Optional[float]]
    section: str
    play_pulse_params: list[Optional[Dict[str, Any]]]
    pulse_pulse_params: list[Optional[Dict[str, Any]]]

    def generate_event_list(
        self,
        start: int,
        max_events: int,
        id_tracker: Iterator[int],
        expand_loops,
        settings: CompilerSettings,
    ) -> List[Dict]:
        assert self.length is not None
        assert self.absolute_start is not None
        assert (
            len(self.pulses)
            == len(self.amplitudes)
            == len(self.set_oscillator_phases)
            == len(self.increment_oscillator_phases)
            == len(self.phases)
            == len(self.oscillator_frequencies)
            == len(self.play_pulse_params)
            == len(self.pulse_pulse_params)
        )

        amplitude_resolution = pow(
            2, getattr(settings, "AMPLITUDE_RESOLUTION_BITS", 24)
        )
        amplitudes = [
            round(amplitude * amplitude_resolution) / amplitude_resolution
            for amplitude in self.amplitudes
        ]

        assert all(
            self.pulses[0].acquire_params.handle == p.acquire_params.handle
            for p in self.pulses
        )
        assert all(
            self.pulses[0].acquire_params.acquisition_type
            == p.acquire_params.acquisition_type
            for p in self.pulses
        )
        start_id = next(id_tracker)
        d = {
            "section_name": self.section,
            "signal": [p.signal.uid for p in self.pulses],
            "play_wave_id": [p.pulse.uid for p in self.pulses],
            "parametrized_with": [],
            "phase": self.phases,
            "amplitude": amplitudes,
            "chain_element_id": start_id,
            "acquisition_type": self.pulses[0].acquire_params.acquisition_type,
            "acquire_handle": self.pulses[0].acquire_params.handle,
        }

        if self.oscillator_frequencies is not None:
            d["oscillator_frequency"] = self.oscillator_frequencies

        if self.pulse_pulse_params:
            d["pulse_pulse_parameters"] = [
                encode_pulse_parameters(par) if par is not None else None
                for par in self.pulse_pulse_params
            ]
        if self.play_pulse_params:
            d["play_pulse_parameters"] = [
                encode_pulse_parameters(par) if par is not None else None
                for par in self.play_pulse_params
            ]

        osc_events = []
        for pulse, iop, sop in zip(
            self.pulses, self.increment_oscillator_phases, self.set_oscillator_phases
        ):
            params_list = []
            for f in ("length", "amplitude", "phase", "offset"):
                if isinstance(getattr(pulse, f), ParameterInfo):
                    params_list.append(getattr(pulse, f).uid)
            d["parametrized_with"].append(params_list)

            osc_common = {
                "time": start,
                "section_name": self.section,
                "signal": pulse.signal.uid,
            }
            if iop:
                osc_events.append(
                    {
                        "event_type": EventType.INCREMENT_OSCILLATOR_PHASE,
                        "increment_oscillator_phase": iop,
                        "id": next(id_tracker),
                        **osc_common,
                    }
                )
            if sop is not None:
                osc_events.append(
                    {
                        "event_type": EventType.SET_OSCILLATOR_PHASE,
                        "set_oscillator_phase": sop,
                        "id": next(id_tracker),
                        **osc_common,
                    }
                )

        return osc_events + [
            {
                "event_type": EventType.ACQUIRE_START,
                "time": start + self.offset,
                "id": start_id,
                **d,
            },
            {
                "event_type": EventType.ACQUIRE_END,
                "time": start + self.length,
                "id": next(id_tracker),
                **d,
            },
        ]

    def _calculate_timing(
        self,
        schedule_data: ScheduleData,  # type: ignore # noqa: F821
        start: int,
        start_may_change: bool,
    ) -> int:
        # Length must be set via parameter, so nothing to do here
        assert self.length is not None

        valid_pulse = next(
            (
                p
                for p in self.pulses
                if p
                and p.pulse is not None
                and p.acquire_params is not None
                and p.acquire_params.handle
            ),
            None,
        )
        if valid_pulse is not None:
            schedule_data.acquire_pulses.setdefault(
                valid_pulse.acquire_params.handle, []
            ).append(self)

        return start

    def __hash__(self):
        return super().__hash__()