import json
import os
from base64 import b64decode
from functools import lru_cache

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()

MAX_PROCESSING_SIZE = 1024 * 1024 * 1024 * 2
MAX_RESULTS = 20
credentials = os.getenv("SERVICE_ACCOUNT_BASE64_JSON")
assert credentials is not None, "SERVICE_ACCOUNT_BASE64_JSON is not set"

client = bigquery.Client.from_service_account_info(json.loads(b64decode(credentials).decode()))


def human_size(
    value: float,
    decimals: int = 2,
    scale: int = 1024,
) -> str:
    unit = "B"
    units = ["B", "kiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB", "RiB", "QiB"]
    for unit in units:
        if value < scale:
            break
        value /= scale
    if int(value) == value:
        # do not return decimals, if the value is already round
        return f"{int(value)} {unit}"
    return f"{round(value * 10**decimals) / 10**decimals}, {unit}"


@lru_cache
def bq_process_size(query: str) -> int | None:
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    query_job = client.query(
        query,
        job_config=job_config,
    )
    query_job.result()
    return query_job.total_bytes_processed if query_job.total_bytes_processed is not None else None


def bq_query(query: str) -> pd.DataFrame:
    size = bq_process_size(query)
    if size is None or size > MAX_PROCESSING_SIZE:
        raise ValueError("Query is too expensive to run")
    client.list_rows
    query_job = client.query(
        query,
    )
    query_job.result()
    destination = query_job.destination
    if destination is None:
        return pd.DataFrame([{"error": "No destination"}])

    rows = client.list_rows(destination, page_token=None, page_size=MAX_RESULTS, max_results=MAX_RESULTS)
    df = pd.DataFrame([dict(row) for row in rows])
    client.delete_table(destination)
    return df


def format_field_type(field: bigquery.SchemaField) -> str:
    if field.mode == "RECORD":
        return ", ".join([f"{sub_field.name}-({format_field_type(sub_field)})" for sub_field in field.fields])
    return field.field_type


def bq_schema(table: str) -> tuple[str | None, pd.DataFrame]:
    table_ref = client.get_table(table)
    schema: list[bigquery.SchemaField] = table_ref.schema
    description = table_ref.description
    return str(description), pd.DataFrame(
        [{field.name: f"{field.mode}: {format_field_type(field)}" for field in schema}]
    )


def list_datasets():
    datasets = list(client.list_datasets())  # Make an API request.
    project = client.project
    if datasets:
        print("Datasets in project {}:".format(project))
        for dataset in datasets:
            print("\t{}".format(dataset.dataset_id))
    else:
        print("{} project does not contain any datasets.".format(project))
