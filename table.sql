-- Target Delta table for Zerobus OpenTelemetry *metrics* ingestion.
-- Zerobus does not create or alter tables -- create this first, then point the OTLP
-- exporter at it via the `x-databricks-zerobus-table-name` header.
--
-- This is the predefined OTLP metrics schema: gauges land in `gauge`, monotonic counters in
-- `sum`, histograms in `histogram`/`exponential_histogram`, and every OTel attribute set is a
-- VARIANT column (queryable via variant_get, e.g. variant_get(gauge.attributes,'$["gpu.id"]','int')).
--
-- Replace <catalog>.<schema> before running.

CREATE SCHEMA IF NOT EXISTS <catalog>.<schema>;

CREATE TABLE IF NOT EXISTS <catalog>.<schema>.gpu_otel_metrics (
  record_id STRING,
  time TIMESTAMP,
  date DATE,
  service_name STRING,
  start_time_unix_nano BIGINT,
  time_unix_nano BIGINT,
  name STRING,
  description STRING,
  unit STRING,
  metric_type STRING,
  gauge STRUCT<value: DOUBLE, exemplars: ARRAY<STRUCT<time_unix_nano: BIGINT, value: DOUBLE, span_id: STRING, trace_id: STRING, filtered_attributes: VARIANT>>, attributes: VARIANT, flags: INT>,
  sum STRUCT<value: DOUBLE, exemplars: ARRAY<STRUCT<time_unix_nano: BIGINT, value: DOUBLE, span_id: STRING, trace_id: STRING, filtered_attributes: VARIANT>>, attributes: VARIANT, flags: INT, aggregation_temporality: STRING, is_monotonic: BOOLEAN>,
  histogram STRUCT<count: BIGINT, sum: DOUBLE, bucket_counts: ARRAY<BIGINT>, explicit_bounds: ARRAY<DOUBLE>, exemplars: ARRAY<STRUCT<time_unix_nano: BIGINT, value: DOUBLE, span_id: STRING, trace_id: STRING, filtered_attributes: VARIANT>>, attributes: VARIANT, flags: INT, min: DOUBLE, max: DOUBLE, aggregation_temporality: STRING>,
  exponential_histogram STRUCT<attributes: VARIANT, count: BIGINT, sum: DOUBLE, scale: INT, zero_count: BIGINT, positive_bucket: STRUCT<offset: INT, bucket_counts: ARRAY<BIGINT>>, negative_bucket: STRUCT<offset: INT, bucket_counts: ARRAY<BIGINT>>, flags: INT, exemplars: ARRAY<STRUCT<time_unix_nano: BIGINT, value: DOUBLE, span_id: STRING, trace_id: STRING, filtered_attributes: VARIANT>>, min: DOUBLE, max: DOUBLE, zero_threshold: DOUBLE, aggregation_temporality: STRING>,
  summary STRUCT<count: BIGINT, sum: DOUBLE, quantile_values: ARRAY<STRUCT<quantile: DOUBLE, value: DOUBLE>>, attributes: VARIANT, flags: INT>,
  metadata VARIANT,
  resource STRUCT<attributes: VARIANT, dropped_attributes_count: INT>,
  resource_schema_url STRING,
  instrumentation_scope STRUCT<name: STRING, version: STRING, attributes: VARIANT, dropped_attributes_count: INT>,
  metric_schema_url STRING)
USING delta
CLUSTER BY (time, service_name)
TBLPROPERTIES (
  'delta.enableVariantShredding' = 'true',
  'delta.feature.variantType-preview' = 'supported',
  'delta.parquet.compression.codec' = 'zstd',
  'otel.schemaVersion' = 'v2');
