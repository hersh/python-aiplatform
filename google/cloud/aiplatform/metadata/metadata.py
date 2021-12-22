# -*- coding: utf-8 -*-

# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from collections import defaultdict
from typing import Dict, Union, Optional

from google.cloud.aiplatform.compat.types import event as gca_event
from google.cloud.aiplatform.metadata import constants
from google.cloud.aiplatform.metadata.artifact import _Artifact
from google.cloud.aiplatform.metadata.context import _Context
from google.cloud.aiplatform.metadata.execution import _Execution
from google.cloud.aiplatform.metadata.metadata_store import _MetadataStore
from google.cloud.aiplatform import pipeline_jobs

# runtime patch to v2 to use new data model
_EXPERIMENT_TRACKING_VERSION = "v2"


class _MetadataService:
    """Contains the exposed APIs to interact with the Managed Metadata Service."""

    def __init__(self):
        self._experiment = None
        self._run = None
        self._metrics = None

        if _EXPERIMENT_TRACKING_VERSION == "v2":
            self._experiment_run = None

    def reset(self):
        """Reset all _MetadataService fields to None"""
        self._experiment = None
        self._run = None
        self._metrics = None

        if _EXPERIMENT_TRACKING_VERSION == "v2":
            self._experiment_run = None

    @property
    def experiment_name(self) -> Optional[str]:
        """Return the experiment name of the _MetadataService, if experiment is not set, return None"""
        if self._experiment:
            return self._experiment.display_name
        return None

    @property
    def run_name(self) -> Optional[str]:
        """Return the run name of the _MetadataService, if run is not set, return None"""
        if _EXPERIMENT_TRACKING_VERSION == "v2":
            if self._experiment_run:
                return self._experiment_run.display_name
        else:
            if self._run:
                return self._run.display_name
        return None

    def set_experiment(self, experiment: str, description: Optional[str] = None):
        """Setup a experiment to current session.

        Args:
            experiment (str):
                Required. Name of the experiment to assign current session with.
            description (str):
                Optional. Description of an experiment.
        Raises:
            ValueError:
                If Context with same name as experiment has already been created with
                a different type.

        """

        _MetadataStore.get_or_create()
        context = _Context.get_or_create(
            resource_id=experiment,
            display_name=experiment,
            description=description,
            schema_title=constants.SYSTEM_EXPERIMENT,
            schema_version=constants.SCHEMA_VERSIONS[constants.SYSTEM_EXPERIMENT],
            metadata=constants.EXPERIMENT_METADATA,
        )
        if context.schema_title != constants.SYSTEM_EXPERIMENT:
            raise ValueError(
                f"Experiment name {experiment} has been used to create other type of resources "
                f"({context.schema_title}) in this MetadataStore, please choose a different experiment name."
            )

        if description and context.description != description:
            context.update(metadata=context.metadata, description=description)

        self.reset()

        self._experiment = context

    def _create_experiment_run_context(self, run: str) -> _Context:
        """Creates an ExperimentRun Context and assigns it as a current Experiment.

        Args:
            run (str): The name of the experiment run.
        Returns:
            _Context: The Context representing this ExperimentRun
        Raises:
            ValueError:
                If name of experiment has already been used in Metadata Store to create another
                Context.
        """
        run_context_id = f"{self._experiment.name}-{run}"

        run_context = _Context.get_or_create(
            resource_id=run_context_id,
            display_name=run,
            schema_title=constants.SYSTEM_EXPERIMENT_RUN,
            schema_version=constants.SCHEMA_VERSIONS[constants.SYSTEM_EXPERIMENT_RUN],
            metadata=constants.EXPERIMENT_METADATA,
        )

        if run_context.schema_title != constants.SYSTEM_EXPERIMENT_RUN:
            raise ValueError(
                f"Run name {run} has been used to create other type of resources ({run_context.schema_title}) "
                "in this MetadataStore, please choose a different run name."
            )

        if self._experiment.resource_name not in run_context.parent_contexts:
            self._experiment.add_context_children([run_context])
            run_context._sync_gca_resource()

        return run_context

    # TODO(b/211012711) add support for resuming runs
    # TODO(b/211013314) add support for returning context manager
    def start_run(self, run: str):
        """Setup a run to current session.

        Args:
            run (str):
                Required. Name of the run to assign current session with.
        Raises:
            ValueError:
                if experiment is not set. Or if run execution or metrics artifact is already created
                but with a different schema.
        """

        if not self._experiment:
            raise ValueError(
                "No experiment set for this run. Make sure to call aiplatform.init(experiment='my-experiment') "
                "before trying to start_run. "
            )

        run_context = None
        if _EXPERIMENT_TRACKING_VERSION == "v2":
            run_context = self._create_experiment_run_context(run=run)

        run_execution_id = f"{self._experiment.name}-{run}"
        run_execution = _Execution.get_or_create(
            resource_id=run_execution_id,
            display_name=run,
            schema_title=constants.SYSTEM_RUN,
            schema_version=constants.SCHEMA_VERSIONS[constants.SYSTEM_RUN],
        )
        if run_execution.schema_title != constants.SYSTEM_RUN:
            raise ValueError(
                f"Run name {run} has been used to create other type of resources ({run_execution.schema_title}) "
                "in this MetadataStore, please choose a different run name."
            )

        if _EXPERIMENT_TRACKING_VERSION == "v2":
            run_context.add_artifacts_and_executions(
                execution_resource_names=[run_execution.resource_name]
            )
        else:
            self._experiment.add_artifacts_and_executions(
                execution_resource_names=[run_execution.resource_name]
            )

        metrics_artifact_id = f"{self._experiment.name}-{run}-metrics"
        metrics_artifact = _Artifact.get_or_create(
            resource_id=metrics_artifact_id,
            display_name=metrics_artifact_id,
            schema_title=constants.SYSTEM_METRICS,
            schema_version=constants.SCHEMA_VERSIONS[constants.SYSTEM_METRICS],
        )
        if metrics_artifact.schema_title != constants.SYSTEM_METRICS:
            raise ValueError(
                f"Run name {run} has been used to create other type of resources ({metrics_artifact.schema_title}) "
                "in this MetadataStore, please choose a different run name."
            )
        run_execution.add_artifact(
            artifact_resource_name=metrics_artifact.resource_name, input=False
        )

        self._run = run_execution
        self._metrics = metrics_artifact

        if _EXPERIMENT_TRACKING_VERSION == "v2":
            self._experiment_run = run_context

    def log_params(self, params: Dict[str, Union[float, int, str]]):
        """Log single or multiple parameters with specified key and value pairs.

        Args:
            params (Dict):
                Required. Parameter key/value pairs.
        """

        self._validate_experiment_and_run(method_name="log_params")
        # query the latest run execution resource before logging.
        execution = _Execution.get_or_create(
            resource_id=self._run.name,
            schema_title=constants.SYSTEM_RUN,
            schema_version=constants.SCHEMA_VERSIONS[constants.SYSTEM_RUN],
        )
        execution.update(metadata=params)

    def log_metrics(self, metrics: Dict[str, Union[float, int]]):
        """Log single or multiple Metrics with specified key and value pairs.

        Args:
            metrics (Dict):
                Required. Metrics key/value pairs. Only flot and int are supported format for value.
        Raises:
            TypeError: If value contains unsupported types.
            ValueError: If Experiment or Run is not set.
        """

        self._validate_experiment_and_run(method_name="log_metrics")
        self._validate_metrics_value_type(metrics)
        # query the latest metrics artifact resource before logging.
        artifact = _Artifact.get_or_create(
            resource_id=self._metrics.name,
            schema_title=constants.SYSTEM_METRICS,
            schema_version=constants.SCHEMA_VERSIONS[constants.SYSTEM_METRICS],
        )
        artifact.update(metadata=metrics)

    def get_experiment_df(
        self, experiment: Optional[str] = None
    ) -> "pd.DataFrame":  # noqa: F821
        """Returns a Pandas DataFrame of the parameters and metrics associated with one experiment.

            Example:

            aiplatform.init(experiment='exp-1')
            aiplatform.start_run(run='run-1')
            aiplatform.log_params({'learning_rate': 0.1})
            aiplatform.log_metrics({'accuracy': 0.9})

            aiplatform.start_run(run='run-2')
            aiplatform.log_params({'learning_rate': 0.2})
            aiplatform.log_metrics({'accuracy': 0.95})

            Will result in the following DataFrame
            ___________________________________________________________________________
            | experiment_name | run_name      | param.learning_rate | metric.accuracy |
            ---------------------------------------------------------------------------
            | exp-1           | run-1         | 0.1                 | 0.9             |
            | exp-1           | run-2         | 0.2                 | 0.95            |
            ---------------------------------------------------------------------------

            Args:
                experiment (str):
                Name of the Experiment to filter results. If not set, return results of current active experiment.

            Returns:
                Pandas Dataframe of Experiment with metrics and parameters.

            Raise:
                NotFound exception if experiment does not exist.
                ValueError if given experiment is not associated with a wrong schema.
            """

        source = "experiment"
        if not experiment:
            experiment = self._experiment.name
            experiment_resource_name = self._experiment.resource_name
        else:
            experiment_resource_name = self._get_experiment_or_pipeline_resource_name(
                name=experiment,
                source=source,
                expected_schema=constants.SYSTEM_EXPERIMENT,
            )

        if _EXPERIMENT_TRACKING_VERSION == "v2":
            return self._query_runs_to_data_frame_v2(
                context_id=experiment,
                context_resource_name=experiment_resource_name,
                source=source,
            )
        else:
            return self._query_runs_to_data_frame(
                context_id=experiment,
                context_resource_name=experiment_resource_name,
                source=source,
            )

    def get_pipeline_df(self, pipeline: str) -> "pd.DataFrame":  # noqa: F821
        """Returns a Pandas DataFrame of the parameters and metrics associated with one pipeline.

        Args:
            pipeline: Name of the Pipeline to filter results.

        Returns:
            Pandas Dataframe of Pipeline with metrics and parameters.

        Raise:
            NotFound exception if experiment does not exist.
            ValueError if given experiment is not associated with a wrong schema.
        """

        source = "pipeline"
        pipeline_resource_name = self._get_experiment_or_pipeline_resource_name(
            name=pipeline, source=source, expected_schema=constants.SYSTEM_PIPELINE
        )

        return self._query_runs_to_data_frame(
            context_id=pipeline,
            context_resource_name=pipeline_resource_name,
            source=source,
        )

    def _validate_experiment_and_run(self, method_name: str):
        if not self._experiment:
            raise ValueError(
                f"No experiment set. Make sure to call aiplatform.init(experiment='my-experiment') "
                f"before trying to {method_name}. "
            )
        if not self._run:
            raise ValueError(
                f"No run set. Make sure to call aiplatform.start_run('my-run') before trying to {method_name}. "
            )

    @staticmethod
    def _validate_metrics_value_type(metrics: Dict[str, Union[float, int]]):
        """Verify that metrics value are with supported types.

        Args:
            metrics (Dict):
                Required. Metrics key/value pairs. Only flot and int are supported format for value.
        Raises:
            TypeError: If value contains unsupported types.
        """

        for key, value in metrics.items():
            if isinstance(value, int) or isinstance(value, float):
                continue
            raise TypeError(
                f"metrics contain unsupported value types. key: {key}; value: {value}; type: {type(value)}"
            )

    @staticmethod
    def _get_experiment_or_pipeline_resource_name(
        name: str, source: str, expected_schema: str
    ) -> str:
        """Get the full resource name of the Context representing an Experiment or Pipeline.

        Args:
            name (str):
                Name of the Experiment or Pipeline.
            source (str):
                Identify whether the this is an Experiment or a Pipeline.
            expected_schema (str):
                expected_schema identifies the expected schema used for Experiment or Pipeline.

        Returns:
            The full resource name of the Experiment or Pipeline Context.

        Raise:
            NotFound exception if experiment or pipeline does not exist.
        """

        context = _Context(resource_name=name)

        if context.schema_title != expected_schema:
            raise ValueError(
                f"Please provide a valid {source} name. {name} is not a {source}."
            )
        return context.resource_name

    def _query_runs_to_data_frame_v2(
        self, context_id: str, context_resource_name: str, source: str
    ) -> "pd.DataFrame":  # noqa: F821
        """Get metrics and parameters associated with a given Context into a Dataframe.

        Compatible with Experiment v2 data model.

        Args:
            context_id (str):
                Name of the Experiment or Pipeline.
            context_resource_name (str):
                Full resource name of the Context associated with an Experiment or Pipeline.
            source (str):
                Identify whether the this is an Experiment or a Pipeline.

        Returns:
            The full resource name of the Experiment or Pipeline Context.

        Raises:
            ImportError: If pandas is not installed.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "Pandas is not installed and is required to get dataframe as the return format. "
                'Please install the SDK using "pip install python-aiplatform[metadata]"'
            )

        filter = f'schema_title="{constants.SYSTEM_EXPERIMENT_RUN}" AND parent_contexts:"{context_resource_name}"'
        run_contexts = _Context.list(filter=filter)

        in_context_query = " OR ".join(
            [f'in_context("{c.resource_name}")' for c in run_contexts]
        )

        filter = f'schema_title="{constants.SYSTEM_RUN}" AND ({in_context_query})'

        run_executions = _Execution.list(filter=filter)

        context_map = {c.name: c for c in run_contexts}

        context_summary = []
        for run_execution in run_executions:
            run_context = context_map[run_execution.name]
            run_dict = {
                f"{source}_name": context_id,
                "run_name": run_context.display_name,
            }
            run_dict.update(
                self._execution_to_column_named_metadata(
                    "param", run_execution.metadata
                )
            )

            for metric_artifact in run_execution.query_input_and_output_artifacts():
                if metric_artifact.schema_title == constants.SYSTEM_METRICS:
                    run_dict.update(
                        self._execution_to_column_named_metadata(
                            "metric", metric_artifact.metadata
                        )
                    )

            context_summary.append(run_dict)

        # get pipelines parameters and metrics

        in_parent_context_query = " OR ".join(
            [f'parent_contexts:"{c.resource_name}"' for c in run_contexts]
        )

        filter = f'schema_title="{constants.SYSTEM_PIPELINE_RUN}" AND ({in_parent_context_query})'

        pipeline_contexts = _Context.list(filter=filter)

        run_context_pipeline_context_pairs = []

        # parent contexts are full resource names
        context_map = {c.resource_name: c for c in run_contexts}

        for pipeline_context in pipeline_contexts:
            pipeline_run_dict = {
                "experiment_name": context_id,
                #"run_name": run_context.display_name,
                "pipeline_run_name": pipeline_context.name,
            }

            context_lineage_subgraph = pipeline_context.query_lineage_subgraph()
            artifact_map = {artifact.name:artifact for artifact in context_lineage_subgraph.artifacts}
            output_execution_map = defaultdict(list)
            for event in context_lineage_subgraph.events:
                if event.type_ == gca_event.Event.Type.OUTPUT:
                    output_execution_map[event.execution].append(event.artifact)

            execution_dicts = []
            for execution in context_lineage_subgraph.executions:
                if execution.schema_title == constants.SYSTEM_RUN:
                    pipeline_params = self._execution_to_column_named_metadata(
                        metadata_type="param",
                        metadata=execution.metadata,
                        filter_prefix=constants.PIPELINE_PARAM_PREFIX) 
                else:
                    execution_dict = pipeline_run_dict.copy()
                    execution_dict['execution_name'] = execution.display_name
                    artifact_dicts = []
                    for artifact_name in output_execution_map[execution.name]:
                        artifact = artifact_map.get(artifact_name)
                        if artifact and artifact.schema_title == constants.SYSTEM_METRICS and artifact.metadata:
                            execution_with_metric_dict = execution_dict.copy()
                            execution_with_metric_dict['output_name'] = artifact.display_name
                            execution_with_metric_dict.update(self._execution_to_column_named_metadata("metric", artifact.metadata))
                            artifact_dicts.append(execution_with_metric_dict)
                    
                    # if this is the only artifact then we only need one row for this execution
                    # otherwise we need to create a row per metric artifact
                    # ignore all executions that didn't create metrics to remove noise
                    if len(artifact_dicts) == 1:
                        execution_dict.update(artifact_dicts[0])
                        execution_dicts.append(execution_dict)
                    elif len(artifact_dicts) >= 1:
                        execution_dicts.extend(artifact_dicts)


            for parent_context_name in pipeline_context.parent_contexts:
                if parent_context_name in context_map:
                    experiment_run = context_map[parent_context_name]
                    this_pipeline_run_dict = pipeline_run_dict.copy()
                    this_pipeline_run_dict["run_name"] = run_context.display_name
                    # if there is only one execution/artifact combo then only need one row for this pipeline
                    # otherwise we need one row be execution/artifact combo
                    if len(execution_dicts) == 1:
                        this_pipeline_run_dict.update(execution_dicts[0])
                        this_pipeline_run_dict.update(pipeline_params)
                        context_summary.append(this_pipeline_run_dict)
                    else:
                        # pipeline params on their own row when there are multiple output metrics
                        pipeline_run_row = this_pipeline_run_dict.copy()
                        pipeline_run_row.update(pipeline_params)
                        context_summary.append(pipeline_run_row)
                        for execution_dict in execution_dicts:
                            execution_dict.update(this_pipeline_run_dict)
                            context_summary.append(execution_dict)

        return pd.DataFrame(context_summary)

    def _query_runs_to_data_frame(
        self, context_id: str, context_resource_name: str, source: str
    ) -> "pd.DataFrame":  # noqa: F821
        """Get metrics and parameters associated with a given Context into a Dataframe.

        Args:
            context_id (str):
                Name of the Experiment or Pipeline.
            context_resource_name (str):
                Full resource name of the Context associated with an Experiment or Pipeline.
            source (str):
                Identify whether the this is an Experiment or a Pipeline.

        Returns:
            The full resource name of the Experiment or Pipeline Context.
        """

        filter = f'schema_title="{constants.SYSTEM_RUN}" AND in_context("{context_resource_name}")'
        run_executions = _Execution.list(filter=filter)

        context_summary = []
        for run_execution in run_executions:
            run_dict = {
                f"{source}_name": context_id,
                "run_name": run_execution.display_name,
            }
            run_dict.update(
                self._execution_to_column_named_metadata(
                    "param", run_execution.metadata
                )
            )

            for metric_artifact in run_execution.query_input_and_output_artifacts():
                run_dict.update(
                    self._execution_to_column_named_metadata(
                        "metric", metric_artifact.metadata
                    )
                )

            context_summary.append(run_dict)

        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "Pandas is not installed and is required to get dataframe as the return format. "
                'Please install the SDK using "pip install python-aiplatform[full]"'
            )

        return pd.DataFrame(context_summary)

    @staticmethod
    def _execution_to_column_named_metadata(
        metadata_type: str, metadata: Dict, filter_prefix: Optional[str] = None
    ) -> Dict[str, Union[int, float, str]]:
        """Returns a dict of the Execution/Artifact metadata with column names.

        Args:
          metadata_type: The type of this execution properties (param, metric).
          metadata: Either an Execution or Artifact metadata field.
          filter_prefix:
            Remove this prefix from the key of metadata field. Mainly used for removing
            "input:" from PipelineJob parameter keys

        Returns:
          Dict of custom properties with keys mapped to column names
        """
        column_key_to_value = {}
        for key, value in metadata.items():
            if filter_prefix and key.startswith(filter_prefix):
                key = key[len(filter_prefix):]
            column_key_to_value[".".join([metadata_type, key])] = value

        return column_key_to_value


    def _log_pipeline_job(self, pipeline_job: pipeline_jobs.PipelineJob):
        
        try:
            pipeline_job.wait_for_resource_creation()
        except Exception as e:
            raise RuntimeError("Could not log PipelineJob to Experiment Run") from e

        resource_name_fields = pipeline_jobs.PipelineJob._parse_resource_name(pipeline_job.resource_name)

        pipeline_job_context = _Context._get(
            resource_name=resource_name_fields['pipeline_job'],
            project=resource_name_fields['project'],
            location=resource_name_fields['location']
        )

        self._experiment_run.add_context_children([pipeline_job_context])        


    def log(self, *, pipeline_job: Optional[pipeline_jobs.PipelineJob]=None):
        if not _EXPERIMENT_TRACKING_VERSION == 'v2':
            raise NotImplementedError('log is not currently supported')

        self._validate_experiment_and_run('log')

        if pipeline_job:
            self._log_pipeline_job(pipeline_job=pipeline_job)





metadata_service = _MetadataService()
