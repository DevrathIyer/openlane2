# Copyright 2023 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import re
import textwrap
from enum import IntEnum
from dataclasses import dataclass
from typing import (
    List,
    Mapping,
    Tuple,
    Dict,
    Any,
    Iterable,
    Optional,
    Union,
)

from .metric import Metric, MetricAggregator, MetricComparisonResult
from ..misc import Filter

modifier_rx = re.compile(r"([\w\-]+)\:([\w\-]+)")


class TableVerbosity(IntEnum):
    """
    The verbosity of the table: whether to include everything, just changes, only
    bad changes or only critical change. Or just nothing.
    """

    NONE = 0
    CRITICAL = 1
    WORSE = 2
    CHANGED = 3
    ALL = 4


def parse_metric_modifiers(metric_name: str) -> Tuple[str, Mapping[str, str]]:
    """
    Parses a metric name into a base and modifiers as specified in
    the METRICS2.1 naming convention.

    :param metric_name: The name of the metric as generated by a utility.
    :returns: A tuple of the base part as a string, then the modifiers as
        a key-value mapping.
    """
    mn_mut = metric_name.split("__")
    modifiers = {}
    while ":" in mn_mut[-1]:
        key, value = mn_mut.pop().split(":", maxsplit=1)
        modifiers[key] = value
    return "__".join(mn_mut), {k: modifiers[k] for k in reversed(modifiers)}


def aggregate_metrics(
    input: Mapping[str, Any],
    aggregator_by_metric: Optional[
        Mapping[str, Union[MetricAggregator, Metric]]
    ] = None,
) -> Dict[str, Any]:
    """
    Takes a set of metrics generated according to the METRICS2.1 naming
    convention.

    :param input: A mapping of strings to values of metrics.
    :param aggregator_by_metric: A mapping of metric names to either:
        - A tuple of the initial accumulator and reducer to aggregate the values from all modifier metrics
        - A :class:`Metric` class
    :returns: A tuple of the base part as a string, then the modifiers as
        a key-value mapping.
    """
    if aggregator_by_metric is None:
        aggregator_by_metric = Metric.by_name

    aggregated: Dict[str, Any] = {}
    for name, value in input.items():
        metric_name, modifiers = parse_metric_modifiers(name)
        if len(modifiers) < 1:
            # No modifiers = final aggregate, don't double-represent in sums
            continue

        modifier_names = list(modifiers.keys())
        dont_aggregate: Iterable[str] = []
        entry = aggregator_by_metric.get(metric_name)
        if isinstance(entry, Metric):
            dont_aggregate = entry.dont_aggregate or []
            entry = entry.aggregator

        if entry is None:
            continue

        if len(set(modifier_names).intersection(set(dont_aggregate))):
            continue

        metric_name_so_far = metric_name
        for modifier in modifier_names:
            start, aggregation_fn = entry
            current = aggregated.get(metric_name_so_far) or start
            aggregated[metric_name_so_far] = aggregation_fn([current, value])
            metric_name_so_far += f"__{modifier}:{modifiers[modifier]}"

    final_values = dict(input)
    final_values.update(aggregated)
    return final_values


def _key_from_metrics(fields: Iterable[str], metric: str) -> List[str]:
    base, modifiers = parse_metric_modifiers(metric)
    result = []
    for field in fields:
        if field == "":
            result.append(base)
        else:
            result.append(modifiers.get(field, ""))
    return result


class MetricDiff(object):
    """
    Aggregates a number of ``MetricComparisonResult`` and allows a number of
    functions to be performed on them.

    :param differences: The metric comparison results.
    """

    @dataclass
    class MetricStatistics:
        """
        A glorified namespace encapsulating a number of statistics of
        :class:`MetricDiff`.

        Should be generated using :meth:`MetricDiff.stats`.

        :param better: The number of datapoints that represent a positive change.
        :param worse: The number of datapoints that represent a negative change.
        :param critical: The number of changes for critical metrics.
        :param unchanged: Values that are unchanged.
        """

        better: int = 0
        worse: int = 0
        critical: int = 0
        unchanged: int = 0

    differences: List[MetricComparisonResult]

    def __init__(self, differences: Iterable[MetricComparisonResult]) -> None:
        self.differences = list(differences)

    def render_md(
        self,
        sort_by: Optional[Iterable[str]] = None,
        table_verbosity: TableVerbosity = TableVerbosity.ALL,
    ) -> str:
        """
        :param sort_by: A list of tuples corresponding to modifiers to sort
            metrics ascendingly by.
        :param table_verbosity: The verbosity of the table: whether to include everything, just changes, only bad changes or only critical changes. Or just nothing.
        :returns: A table of the differences in Markdown format.
        """
        if table_verbosity == TableVerbosity.NONE:
            return ""

        differences = self.differences
        if fields := sort_by:
            differences = sorted(
                differences,
                key=lambda x: _key_from_metrics(fields, x.metric_name),  # type: ignore # (mypy bug)
            )

        table = ""

        changed = []
        worse = []
        critical = []
        remaining = []

        for row in differences:
            if row.critical is True:
                critical.append(row)
            elif row.better is False:
                worse.append(row)
            elif row.is_changed():
                changed.append(row)
            else:
                remaining.append(row)

        listed_differences: List[MetricComparisonResult] = []
        if table_verbosity >= TableVerbosity.CRITICAL:
            listed_differences += critical
        if table_verbosity >= TableVerbosity.WORSE:
            listed_differences += worse
        if table_verbosity >= TableVerbosity.CHANGED:
            listed_differences += changed
        if table_verbosity >= TableVerbosity.ALL:
            listed_differences += remaining

        if len(listed_differences) > 0:
            table = textwrap.dedent(
                f"""
                | {'Metric':<70} | {'Before':<10} | {'After':<10} | {'Delta':<20} |
                | {'-':<70} | {'-':<10} | {'-':<10} | {'-':<20} |
                """
            )

            for row in listed_differences:
                before, after, delta = row.format_values()
                emoji = ""
                if row.better is not None:
                    if row.better:
                        emoji = " ⭕"
                    else:
                        emoji = " ❗"
                if row.critical and row.is_changed():
                    emoji = " ‼️"
                table += f"| {row.metric_name:<70} | {before:<10} | {after:<10} | {f'{delta}{emoji}':<20} |\n"

        return table

    def stats(self) -> MetricStatistics:
        """
        :returns: A :class:`MetricStatistics` object based on this aggregate.
        """
        stats = MetricDiff.MetricStatistics()
        for row in self.differences:
            if not row.is_changed():
                stats.unchanged += 1
            elif row.better is not None:
                if row.better:
                    stats.better += 1
                else:
                    stats.worse += 1
            if row.critical:
                stats.critical += 1
        return stats

    @classmethod
    def from_metrics(
        Self,
        gold: dict,
        new: dict,
        significant_figures: int,
        filter: Filter = Filter(["*"]),
    ) -> "MetricDiff":
        """
        Creates a :class:`MetricDiff` object from two sets of metrics.

        :param gold: The "gold-standard" metrics to compare against
        :param new: The metrics being evaluated
        :param filter: A :class:`Filter` for the names of the metrics to include
            or exclude certain metrics.
        :returns: The aggregate of the differences between gold and good
        """

        def generator(g, n):
            for metric in filter.filter(sorted(n.keys())):
                if metric not in g:
                    continue
                base_metric, modifiers = parse_metric_modifiers(metric)
                lhs_value, rhs_value = g[metric], n[metric]
                if type(lhs_value) != type(rhs_value):
                    lhs_value = type(rhs_value)(lhs_value)

                if metric_object := Metric.by_name.get(base_metric):
                    yield metric_object.compare(
                        lhs_value, rhs_value, significant_figures, modifiers=modifiers
                    )

        return MetricDiff(generator(gold, new))
