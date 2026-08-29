-- Business/transformation test: the mart must not create or lose revenue.
--
-- Total daily_revenue in the mart must equal the raw sum of completed-order
-- amounts in staging. If the customer join fans out (multiple active rows per
-- customer) the mart total inflates and this test returns a row.

with mart_total as (
    select coalesce(sum(daily_revenue), 0) as revenue
    from {{ ref('fct_daily_revenue') }}
),
staging_total as (
    select coalesce(sum(amount_usd), 0) as revenue
    from {{ ref('stg_orders') }}
    where status = 'completed'
)
select
    m.revenue as mart_revenue,
    s.revenue as staging_revenue,
    m.revenue - s.revenue as diff
from mart_total m
cross join staging_total s
where abs(m.revenue - s.revenue) > 0.01
