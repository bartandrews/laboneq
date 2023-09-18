# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import StringIO

from rich import box
from rich.console import Console
from rich.table import Table

from laboneq.compiler.common.awg_info import AwgKey
from laboneq.compiler.workflow.realtime_compiler import RealtimeCompilerOutput
from laboneq.compiler.workflow.rt_linker import CombinedRealtimeCompilerOutput

_logger = logging.getLogger(__name__)


@dataclass(order=True)
class ReportEntry:
    nt_step_indices: tuple[int, ...]
    awg: AwgKey | None
    seqc_loc: int
    command_table_entries: int
    wave_indices: int
    waveform_samples: int


class CompilationReportGenerator:
    def __init__(self):
        self._data: list[ReportEntry] = []
        self._total: ReportEntry | None = None

    def update(
        self, rt_compiler_output: RealtimeCompilerOutput, step_indices: list[int]
    ):
        for awg, awg_src in rt_compiler_output.src.items():
            seqc_loc = len(awg_src["text"].splitlines())
            ct = rt_compiler_output.command_tables.get(awg, {"ct": []})["ct"]
            ct_len = len(ct)
            wave_indices = rt_compiler_output.wave_indices[awg]["value"]
            wave_indices_count = len(wave_indices)
            sample_count = 0
            for wave_index in wave_indices.items():
                sample_count += self._count_samples(
                    rt_compiler_output.waves, wave_index
                )

            self._data.append(
                ReportEntry(
                    awg=awg,
                    nt_step_indices=tuple(step_indices),
                    seqc_loc=seqc_loc,
                    command_table_entries=ct_len,
                    wave_indices=wave_indices_count,
                    waveform_samples=sample_count,
                )
            )

    def _count_samples(self, waves, wave_index):
        multiplier = 1
        wave_name, (_, wave_type) = wave_index
        if wave_type in ("iq", "double", "multi"):
            waveform_name = f"{wave_name}_i.wave"
            multiplier = 2  # two samples per clock cycle
        elif wave_type == "complex":
            waveform_name = f"{wave_name}.wave"
            multiplier = 2  # two samples per clock cycle
        elif wave_type == "single":
            waveform_name = f"{wave_name}.wave"

        waveform = waves[waveform_name]
        return len(waveform["samples"]) * multiplier

    def calculate_total(self, compiler_output: CombinedRealtimeCompilerOutput):
        total_seqc = sum(len(s["text"].splitlines()) for s in compiler_output.src)
        total_ct = sum(len(ct["ct"]) for ct in compiler_output.command_tables)
        total_wave_idx = sum(len(wi) for wi in compiler_output.wave_indices)
        total_samples = sum(
            self._count_samples(compiler_output.waves, wi)
            for wil in compiler_output.wave_indices
            for wi in wil["value"].items()
        )

        self._total = ReportEntry(
            nt_step_indices=(),
            awg=None,
            seqc_loc=total_seqc,
            command_table_entries=total_ct,
            wave_indices=total_wave_idx,
            waveform_samples=total_samples,
        )

    def create_table(self) -> Table:
        entries = sorted(self._data)
        all_nt_steps = set(e.nt_step_indices for e in entries)
        include_nt_step = len(all_nt_steps) > 1

        table = Table(box=box.HORIZONTALS)

        if include_nt_step:
            table.add_column("Step")

        table.add_column("Device")
        table.add_column("AWG", justify="right")
        table.add_column("SeqC LOC", justify="right")
        table.add_column("CT entries", justify="right")
        table.add_column("Waveforms", justify="right")
        table.add_column("Samples", justify="right")

        previous_nt_step = None
        previous_device = None

        for entry in entries:
            cells = []
            if include_nt_step:
                if entry.nt_step_indices != previous_nt_step:
                    table.add_section()
                    cells.append(",".join(str(n) for n in entry.nt_step_indices))
                    previous_nt_step = entry.nt_step_indices
                else:
                    cells.append("")

            if previous_device != entry.awg.device_id or not include_nt_step:
                device_cell = f"{entry.awg.device_id}"
            else:
                device_cell = ""

            cells.extend(
                [
                    device_cell,
                    f"{entry.awg.awg_number}",
                    f"{entry.seqc_loc}",
                    f"{entry.command_table_entries}",
                    f"{entry.wave_indices}",
                    f"{entry.waveform_samples}",
                ]
            )
            table.add_row(*cells)

        if self._total is not None:
            table.show_footer = True
            cells = []
            if include_nt_step:
                cells.append("")
            cells.extend(
                [
                    "TOTAL",  # device id
                    "",  # AWG idx
                    f"{self._total.seqc_loc}",
                    f"{self._total.command_table_entries}",
                    "",  #  f"{self._total.wave_indices}",
                    f"{self._total.waveform_samples}",
                ]
            )
            for column, cell in zip(table.columns, cells):
                column.footer = cell

        return table

    def __str__(self):
        table = self.create_table()
        with StringIO() as buffer:
            console = Console(file=buffer, force_jupyter=False)
            console.print(table)
            return buffer.getvalue()

    def log_report(self):
        for line in str(self).splitlines():
            _logger.info(line)