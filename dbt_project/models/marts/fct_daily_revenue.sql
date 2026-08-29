-- Daily completed-order revenue for the CEO dashboard.
--
-- The customer dimension can carry SCD history. If more than one row per
-- customer is flagged is_active = true, the join below fans out and inflates
-- revenue *without any SQL error*. We defend against that by collapsing the
-- dimension to exactly one active row per customer before joining.
-- See tests/assert_revenue_reconciles.sql and unit_tests.yml.

with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
active_customers as (
    select customer_id
    from {{ ref('stg_customers') }}
    where is_active = true
    qualify row_number() over (partition by customer_id order by valid_from desc) = 1
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
