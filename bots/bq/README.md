# Service account
Create a service account json from [IAM & Admin](https://console.cloud.google.com/iam-admin/cloudidentity/consumer) > Service accounts > Create service account.

The account should have only the roles:
- BigQuery Data Viewer `roles/bigquery.dataViewer`
- BigQuery Job User `roles/bigquery.jobUser`

# Usage example

```sql
SELECT refresh_date AS DAY,
       term AS Top_Term,
       rank,
FROM `bigquery-public-data.google_trends.top_terms`
WHERE refresh_date >= DATE_SUB(CURRENT_DATE(),INTERVAL 2 WEEK)
  AND rank = 1
GROUP BY DAY,
         Top_Term,
         rank
ORDER BY DAY DESC
```
should run

```sql
SELECT * FROM `bigquery-public-data.google_trends.top_terms` WHERE rank > 0
```
should not (2gb limit error)
