"""Create Day 4 Product operation, audit, readback, and Trace objects.

Revision ID: 0004_day4_product_operations
Revises: 0003_day3_runtime_state
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_day4_product_operations"
down_revision: str | None = "0003_day3_runtime_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMMAND_TYPES = (
    "'open_seller_risk_case','create_investigation_task',"
    "'create_investigation_from_alert_hit','create_and_enable_metric_alert_rule',"
    "'assign_investigation','transition_investigation',"
    "'add_investigation_conclusion','close_investigation'"
)
_PROPOSAL_STATUSES = "'pending','approved','rejected','expired','superseded'"
_EXECUTION_STATUSES = (
    "'not_started','succeeded','failed_retryable','failed_final'"
)
_INVESTIGATION_STATUSES = "'open','in_progress','blocked','resolved','closed'"
_RISK_STATUSES = "'under_investigation','confirmed','dismissed','inconclusive'"
_SCENARIO_CHECK = "scenario_id ~ '^[a-z][a-z0-9-]{2,127}-v[0-9]+$'"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _timestamp() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _security_definer_function(
    name: str,
    arguments: str,
    returns: str,
    body: str,
    *,
    language: str = "plpgsql",
) -> str:
    return f"""
CREATE FUNCTION trusted_schema.{name}(
{arguments}
)
RETURNS {returns}
LANGUAGE {language}
SECURITY DEFINER
SET search_path = trusted_schema, pg_temp
AS $function$
{body}
$function$
""".strip()


_COMMON_COMMAND_ARGUMENTS = """    p_execution_id uuid,
    p_proposal_id uuid,
    p_proposal_version integer,
    p_actor_id uuid,
    p_payload_sha256 text,
    p_target_versions_sha256 text,
    p_nonce_digest text,
    p_idempotency_key text,
    p_scenario_id text"""

_COMMAND_RETURN = """TABLE (
    execution_id uuid,
    before_version integer,
    after_version integer,
    result_code text,
    committed_at timestamptz,
    public_summary text,
    audit_ref text
)"""


def _command_function(
    name: str,
    command_type: str,
    specific_arguments: str,
    mutation: str,
) -> str:
    arguments = _COMMON_COMMAND_ARGUMENTS
    if specific_arguments:
        arguments += ",\n" + specific_arguments
    body = f"""DECLARE
    v_existing ops.command_execution%ROWTYPE;
    v_proposal ops.operation_proposal%ROWTYPE;
    v_decision ops.approval_decision%ROWTYPE;
    v_claimed_nonce text;
    v_before_version integer;
    v_after_version integer;
    v_public_target_refs jsonb;
    v_public_summary text;
    v_committed_at timestamptz;
    v_audit_ref text;
BEGIN
    SELECT execution.*
      INTO v_existing
      FROM ops.command_execution AS execution
     WHERE execution.proposal_id = p_proposal_id
       AND execution.proposal_version = p_proposal_version
     FOR UPDATE;

    IF FOUND THEN
        IF v_existing.execution_id <> p_execution_id
           OR v_existing.actor_id <> p_actor_id
           OR v_existing.idempotency_key <> p_idempotency_key
           OR v_existing.status <> 'succeeded' THEN
            RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
                MESSAGE = 'execution_identity_conflict';
        END IF;
        RETURN QUERY SELECT
            v_existing.execution_id,
            v_existing.before_version,
            v_existing.after_version,
            v_existing.result_code,
            v_existing.committed_at,
            v_existing.public_summary,
            v_existing.audit_ref;
        RETURN;
    END IF;

    SELECT proposal.*
      INTO v_proposal
      FROM ops.operation_proposal AS proposal
     WHERE proposal.proposal_id = p_proposal_id
       AND proposal.proposal_version = p_proposal_version
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'no_data_found', MESSAGE = 'proposal_not_found';
    END IF;

    SELECT decision.*
      INTO v_decision
      FROM ops.approval_decision AS decision
     WHERE decision.proposal_id = p_proposal_id
       AND decision.proposal_version = p_proposal_version;
    IF NOT FOUND
       OR v_proposal.status <> 'approved'
       OR v_proposal.command_type <> '{command_type}'
       OR v_proposal.payload_sha256 <> p_payload_sha256
       OR v_proposal.target_versions_sha256 <> p_target_versions_sha256
       OR v_proposal.scenario_id <> p_scenario_id
       OR v_proposal.expires_at <= transaction_timestamp()
       OR v_decision.decision <> 'approve'
       OR v_decision.requester_id <> v_proposal.requester_id
       OR v_decision.approver_id <> p_actor_id THEN
        RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
            MESSAGE = 'approval_binding_invalid';
    END IF;

    INSERT INTO ops.command_execution (
        execution_id, proposal_id, proposal_version, actor_id, command_type,
        status, idempotency_key, started_at, scenario_id
    ) VALUES (
        p_execution_id, p_proposal_id, p_proposal_version, p_actor_id, '{command_type}',
        'not_started', p_idempotency_key, transaction_timestamp(), p_scenario_id
    );

    UPDATE ops.approval_nonce
       SET used_at = transaction_timestamp()
     WHERE nonce_digest = p_nonce_digest
       AND proposal_id = p_proposal_id
       AND proposal_version = p_proposal_version
       AND approver_id = p_actor_id
       AND payload_sha256 = p_payload_sha256
       AND target_versions_sha256 = p_target_versions_sha256
       AND scenario_id = p_scenario_id
       AND used_at IS NULL
       AND expires_at > transaction_timestamp()
    RETURNING nonce_digest INTO v_claimed_nonce;
    IF v_claimed_nonce IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
            MESSAGE = 'approval_nonce_invalid';
    END IF;

{mutation}

    v_committed_at := transaction_timestamp();
    v_audit_ref := 'audit:' || p_execution_id::text;
    UPDATE ops.command_execution AS execution
       SET status = 'succeeded',
           before_version = v_before_version,
           after_version = v_after_version,
           result_code = 'operation_succeeded',
           committed_at = v_committed_at,
           public_summary = v_public_summary,
           audit_ref = v_audit_ref
     WHERE execution.execution_id = p_execution_id;

    PERFORM trusted_schema.append_audit_event(
        p_execution_id, p_execution_id, 'operation_executed', 1,
        p_proposal_id, p_proposal_version, p_actor_id, '{command_type}',
        p_payload_sha256, v_public_target_refs, v_before_version,
        v_after_version, 'operation_succeeded', v_committed_at, p_scenario_id
    );

    RETURN QUERY SELECT
        p_execution_id, v_before_version, v_after_version, 'operation_succeeded',
        v_committed_at, v_public_summary, v_audit_ref;
END;"""
    return _security_definer_function(name, arguments, _COMMAND_RETURN, body)


def _create_control_functions() -> None:
    op.execute(
        _security_definer_function(
            "create_operation_proposal",
            """    p_proposal_id uuid,
    p_proposal_version integer,
    p_requester_id uuid,
    p_command_type text,
    p_command_schema_version integer,
    p_canonical_payload jsonb,
    p_payload_sha256 text,
    p_evidence_refs jsonb,
    p_preview jsonb,
    p_target_versions jsonb,
    p_target_versions_sha256 text,
    p_expires_at timestamptz,
    p_idempotency_key text,
    p_private_seller_id text,
    p_private_evidence_digest text,
    p_scenario_id text""",
            "TABLE (proposal_id uuid, proposal_version integer, status text)",
            """DECLARE
    v_existing ops.operation_proposal%ROWTYPE;
    v_previous ops.operation_proposal%ROWTYPE;
BEGIN
    SELECT proposal.*
      INTO v_existing
      FROM ops.operation_proposal AS proposal
     WHERE proposal.requester_id = p_requester_id
       AND proposal.idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        IF v_existing.command_type <> p_command_type
           OR v_existing.payload_sha256 <> p_payload_sha256
           OR v_existing.private_seller_id IS DISTINCT FROM p_private_seller_id
           OR v_existing.private_evidence_digest
                IS DISTINCT FROM p_private_evidence_digest THEN
            RAISE EXCEPTION USING ERRCODE = 'unique_violation',
                MESSAGE = 'proposal_idempotency_conflict';
        END IF;
        RETURN QUERY SELECT
            v_existing.proposal_id, v_existing.proposal_version, v_existing.status;
        RETURN;
    END IF;

    IF p_proposal_version > 1 THEN
        SELECT proposal.*
          INTO v_previous
          FROM ops.operation_proposal AS proposal
         WHERE proposal.proposal_id = p_proposal_id
           AND proposal.proposal_version = p_proposal_version - 1
         FOR UPDATE;
        IF NOT FOUND OR v_previous.requester_id <> p_requester_id
           OR v_previous.status <> 'pending' THEN
            RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
                MESSAGE = 'proposal_revision_conflict';
        END IF;
        UPDATE ops.operation_proposal AS proposal
           SET status = 'superseded', updated_at = transaction_timestamp()
         WHERE proposal.proposal_id = p_proposal_id
           AND proposal.proposal_version = p_proposal_version - 1;
    ELSIF EXISTS (
        SELECT 1 FROM ops.operation_proposal AS proposal
         WHERE proposal.proposal_id = p_proposal_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'proposal_version_conflict';
    END IF;

    IF (
        p_command_type = 'open_seller_risk_case'
        AND (
            p_private_seller_id IS NULL
            OR p_private_evidence_digest IS NULL
            OR p_private_evidence_digest !~ '^[0-9a-f]{64}$'
            OR NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(p_evidence_refs) AS evidence
                WHERE evidence->>'kind' = 'query'
                  AND evidence->>'digest' = p_private_evidence_digest
            )
        )
    ) OR (
        p_command_type <> 'open_seller_risk_case'
        AND (p_private_seller_id IS NOT NULL OR p_private_evidence_digest IS NOT NULL)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
            MESSAGE = 'proposal_private_binding_invalid';
    END IF;

    INSERT INTO ops.operation_proposal (
        proposal_id, proposal_version, requester_id, command_type,
        command_schema_version, canonical_payload, payload_sha256, evidence_refs,
        preview, target_versions, target_versions_sha256, status, expires_at,
        idempotency_key, private_seller_id, private_evidence_digest,
        scenario_id, created_at, updated_at
    ) VALUES (
        p_proposal_id, p_proposal_version, p_requester_id, p_command_type,
        p_command_schema_version, p_canonical_payload, p_payload_sha256,
        p_evidence_refs, p_preview, p_target_versions, p_target_versions_sha256,
        'pending', p_expires_at, p_idempotency_key,
        p_private_seller_id, p_private_evidence_digest, p_scenario_id,
        transaction_timestamp(), transaction_timestamp()
    );
    RETURN QUERY SELECT p_proposal_id, p_proposal_version, 'pending'::text;
END;""",
        )
    )

    op.execute(
        _security_definer_function(
            "record_approval_decision",
            """    p_proposal_id uuid,
    p_proposal_version integer,
    p_requester_id uuid,
    p_approver_id uuid,
    p_decision text,
    p_reason text,
    p_nonce_digest text,
    p_payload_sha256 text,
    p_target_versions_sha256 text,
    p_key_version integer,
    p_grant_expires_at timestamptz,
    p_grant_sha256 text,
    p_scenario_id text""",
            "TABLE (proposal_id uuid, proposal_version integer, status text, decided_at timestamptz)",
            """DECLARE
    v_proposal ops.operation_proposal%ROWTYPE;
    v_decided_at timestamptz := transaction_timestamp();
BEGIN
    SELECT proposal.*
      INTO v_proposal
      FROM ops.operation_proposal AS proposal
     WHERE proposal.proposal_id = p_proposal_id
       AND proposal.proposal_version = p_proposal_version
     FOR UPDATE;
    IF NOT FOUND OR v_proposal.status <> 'pending'
       OR v_proposal.requester_id <> p_requester_id
       OR v_proposal.payload_sha256 <> p_payload_sha256
       OR v_proposal.target_versions_sha256 <> p_target_versions_sha256
       OR v_proposal.scenario_id <> p_scenario_id
       OR v_proposal.expires_at <= v_decided_at
       OR p_requester_id = p_approver_id THEN
        RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
            MESSAGE = 'approval_decision_invalid';
    END IF;
    IF p_decision = 'approve' AND (
        p_nonce_digest IS NULL OR p_key_version IS NULL
        OR p_grant_expires_at <= v_decided_at
        OR p_grant_expires_at > v_proposal.expires_at
        OR p_grant_sha256 IS NULL
        OR p_grant_sha256 !~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'approval_grant_invalid';
    END IF;
    IF p_decision = 'reject' AND (
        p_nonce_digest IS NOT NULL
        OR p_key_version IS NOT NULL
        OR p_grant_expires_at IS NOT NULL
        OR p_grant_sha256 IS NOT NULL
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'rejection_grant_forbidden';
    END IF;

    INSERT INTO ops.approval_decision (
        proposal_id, proposal_version, decision, requester_id, approver_id,
        reason, decided_at, grant_sha256, scenario_id
    ) VALUES (
        p_proposal_id, p_proposal_version, p_decision, p_requester_id,
        p_approver_id, p_reason, v_decided_at, p_grant_sha256, p_scenario_id
    );
    IF p_decision = 'approve' THEN
        INSERT INTO ops.approval_nonce (
            nonce_digest, proposal_id, proposal_version, approver_id,
            payload_sha256, target_versions_sha256, key_version, expires_at,
            scenario_id
        ) VALUES (
            p_nonce_digest, p_proposal_id, p_proposal_version, p_approver_id,
            p_payload_sha256, p_target_versions_sha256, p_key_version,
            p_grant_expires_at, p_scenario_id
        );
    END IF;
    UPDATE ops.operation_proposal
       SET status = CASE p_decision WHEN 'approve' THEN 'approved' ELSE 'rejected' END,
           updated_at = v_decided_at
     WHERE operation_proposal.proposal_id = p_proposal_id
       AND operation_proposal.proposal_version = p_proposal_version;
    RETURN QUERY SELECT
        p_proposal_id,
        p_proposal_version,
        CASE p_decision WHEN 'approve' THEN 'approved' ELSE 'rejected' END,
        v_decided_at;
END;""",
        )
    )

    op.execute(
        _security_definer_function(
            "store_alert_backtest",
            """    p_backtest_ref uuid,
    p_rule_spec_sha256 text,
    p_evidence_sha256 text,
    p_window_results jsonb,
    p_coverage numeric,
    p_scenario_id text,
    p_created_at timestamptz""",
            "TABLE (backtest_ref uuid, rule_spec_sha256 text)",
            """DECLARE
    v_existing ops.alert_backtest_result%ROWTYPE;
BEGIN
    SELECT result.*
      INTO v_existing
      FROM ops.alert_backtest_result AS result
     WHERE result.backtest_ref = p_backtest_ref
     FOR UPDATE;
    IF FOUND THEN
        IF v_existing.rule_spec_sha256 <> p_rule_spec_sha256
           OR v_existing.evidence_sha256 <> p_evidence_sha256
           OR v_existing.window_results <> p_window_results
           OR v_existing.coverage <> p_coverage
           OR v_existing.scenario_id <> p_scenario_id THEN
            RAISE EXCEPTION USING ERRCODE = 'unique_violation',
                MESSAGE = 'backtest_identity_conflict';
        END IF;
    ELSE
        INSERT INTO ops.alert_backtest_result (
            backtest_ref, rule_spec_sha256, evidence_sha256, window_results,
            coverage, scenario_id, created_at
        ) VALUES (
            p_backtest_ref, p_rule_spec_sha256, p_evidence_sha256,
            p_window_results, p_coverage, p_scenario_id, p_created_at
        );
    END IF;
    RETURN QUERY SELECT p_backtest_ref, p_rule_spec_sha256;
END;""",
        )
    )

    op.execute(
        _security_definer_function(
            "read_operation_proposal",
            """    p_proposal_id uuid,
    p_proposal_version integer,
    p_scenario_id text""",
            """TABLE (
    proposal_id uuid,
    proposal_version integer,
    requester_id uuid,
    command_type text,
    command_schema_version integer,
    canonical_payload jsonb,
    payload_sha256 text,
    evidence_refs jsonb,
    preview jsonb,
    target_versions jsonb,
    status text,
    expires_at timestamptz,
    idempotency_key text,
    private_seller_id text,
    private_evidence_digest text,
    created_at timestamptz
)""",
            """BEGIN
    RETURN QUERY
    SELECT
        proposal.proposal_id,
        proposal.proposal_version,
        proposal.requester_id,
        proposal.command_type,
        proposal.command_schema_version,
        proposal.canonical_payload,
        proposal.payload_sha256,
        proposal.evidence_refs,
        proposal.preview,
        proposal.target_versions,
        proposal.status,
        proposal.expires_at,
        proposal.idempotency_key,
        proposal.private_seller_id,
        proposal.private_evidence_digest,
        proposal.created_at
    FROM ops.operation_proposal AS proposal
    WHERE proposal.proposal_id = p_proposal_id
      AND proposal.proposal_version = p_proposal_version
      AND proposal.scenario_id = p_scenario_id;
END;""",
        )
    )

    op.execute(
        _security_definer_function(
            "read_approval_decision",
            """    p_proposal_id uuid,
    p_proposal_version integer,
    p_scenario_id text""",
            """TABLE (
    proposal_id uuid,
    proposal_version integer,
    status text,
    approver_id uuid,
    reason text,
    decided_at timestamptz,
    payload_sha256 text,
    requester_id uuid,
    target_versions jsonb,
    nonce_digest text,
    key_version integer,
    grant_expires_at timestamptz,
    grant_sha256 text
)""",
            """BEGIN
    RETURN QUERY
    SELECT
        decision.proposal_id,
        decision.proposal_version,
        proposal.status,
        decision.approver_id,
        decision.reason,
        decision.decided_at,
        proposal.payload_sha256,
        proposal.requester_id,
        proposal.target_versions,
        nonce.nonce_digest,
        nonce.key_version,
        nonce.expires_at,
        decision.grant_sha256
    FROM ops.approval_decision AS decision
    JOIN ops.operation_proposal AS proposal
      ON proposal.proposal_id = decision.proposal_id
     AND proposal.proposal_version = decision.proposal_version
     AND proposal.scenario_id = decision.scenario_id
    LEFT JOIN ops.approval_nonce AS nonce
      ON nonce.proposal_id = decision.proposal_id
     AND nonce.proposal_version = decision.proposal_version
     AND nonce.scenario_id = decision.scenario_id
    WHERE decision.proposal_id = p_proposal_id
      AND decision.proposal_version = p_proposal_version
      AND decision.scenario_id = p_scenario_id;
END;""",
        )
    )


def _create_read_functions() -> None:
    op.execute(
        _security_definer_function(
            "find_seller_target_candidates",
            """    p_metric_ref text,
    p_observed_from timestamptz,
    p_observed_to timestamptz,
    p_threshold numeric,
    p_minimum_denominator bigint,
    p_status_filters text[]""",
            "TABLE (seller_id text, numerator bigint, denominator bigint, metric_value numeric)",
            """BEGIN
    IF p_metric_ref NOT IN (
        'late_delivery_rate', 'low_rating_rate', 'cancellation_rate'
    ) OR p_observed_from >= p_observed_to THEN
        RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
            MESSAGE = 'seller_target_request_invalid';
    END IF;
    RETURN QUERY
    WITH observations AS (
        SELECT
            item.seller_id,
            COUNT(DISTINCT orders.order_id)::bigint AS denominator,
            COUNT(DISTINCT orders.order_id) FILTER (
                WHERE CASE p_metric_ref
                    WHEN 'late_delivery_rate' THEN
                        orders.order_delivered_customer_date
                            > orders.order_estimated_delivery_date
                    WHEN 'low_rating_rate' THEN reviews.review_score <= 2
                    WHEN 'cancellation_rate' THEN orders.order_status = 'canceled'
                    ELSE false
                END
            )::bigint AS numerator
        FROM retail.order_items AS item
        JOIN retail.orders AS orders ON orders.order_id = item.order_id
        LEFT JOIN retail.order_reviews AS reviews ON reviews.order_id = orders.order_id
        WHERE orders.order_purchase_timestamp >= p_observed_from
          AND orders.order_purchase_timestamp < p_observed_to
          AND orders.order_status = ANY(p_status_filters)
        GROUP BY item.seller_id
    )
    SELECT
        observations.seller_id,
        observations.numerator,
        observations.denominator,
        observations.numerator::numeric / NULLIF(observations.denominator, 0)
    FROM observations
    WHERE observations.denominator >= p_minimum_denominator
      AND observations.numerator::numeric / NULLIF(observations.denominator, 0)
          >= p_threshold
    ORDER BY observations.seller_id;
END;""",
        )
    )

    read_specs = {
        "read_late_delivery_backtest": (
            "orders.order_delivered_customer_date > orders.order_estimated_delivery_date",
            "orders.order_delivered_customer_date IS NOT NULL",
        ),
        "read_cancellation_backtest": (
            "orders.order_status = 'canceled'",
            "true",
        ),
    }
    for name, (numerator_filter, denominator_filter) in read_specs.items():
        op.execute(
            _security_definer_function(
                name,
                """    p_started_at timestamptz,
    p_ended_at timestamptz,
    p_window text,
    p_minimum_denominator bigint,
    p_status_filters text[]""",
                """TABLE (
    window_start timestamptz,
    window_end timestamptz,
    numerator bigint,
    denominator bigint,
    value numeric,
    coverage numeric
)""",
                f"""BEGIN
    IF p_started_at >= p_ended_at OR p_window NOT IN ('week', 'month') THEN
        RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
            MESSAGE = 'backtest_request_invalid';
    END IF;
    RETURN QUERY
    SELECT
        date_trunc(p_window, orders.order_purchase_timestamp) AS window_start,
        date_trunc(p_window, orders.order_purchase_timestamp)
            + CASE p_window WHEN 'week' THEN interval '1 week' ELSE interval '1 month' END,
        COUNT(*) FILTER (WHERE {numerator_filter})::bigint AS numerator,
        COUNT(*) FILTER (WHERE {denominator_filter})::bigint AS denominator,
        COUNT(*) FILTER (WHERE {numerator_filter})::numeric
            / NULLIF(COUNT(*) FILTER (WHERE {denominator_filter}), 0),
        CASE
            WHEN COUNT(*) FILTER (WHERE {denominator_filter}) >= p_minimum_denominator
            THEN 1::numeric ELSE 0::numeric
        END AS coverage
    FROM retail.orders AS orders
    WHERE orders.order_purchase_timestamp >= p_started_at
      AND orders.order_purchase_timestamp < p_ended_at
      AND orders.order_status = ANY(p_status_filters)
    GROUP BY window_start
    ORDER BY window_start;
END;""",
            )
        )

    op.execute(
        _security_definer_function(
            "read_low_rating_backtest",
            """    p_started_at timestamptz,
    p_ended_at timestamptz,
    p_window text,
    p_minimum_denominator bigint,
    p_status_filters text[]""",
            """TABLE (
    window_start timestamptz,
    window_end timestamptz,
    numerator bigint,
    denominator bigint,
    value numeric,
    coverage numeric
)""",
            """BEGIN
    IF p_started_at >= p_ended_at OR p_window NOT IN ('week', 'month') THEN
        RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
            MESSAGE = 'backtest_request_invalid';
    END IF;
    RETURN QUERY
    SELECT
        date_trunc(p_window, reviews.review_creation_date) AS window_start,
        date_trunc(p_window, reviews.review_creation_date)
            + CASE p_window WHEN 'week' THEN interval '1 week' ELSE interval '1 month' END,
        COUNT(*) FILTER (WHERE reviews.review_score <= 2)::bigint AS numerator,
        COUNT(*)::bigint AS denominator,
        COUNT(*) FILTER (WHERE reviews.review_score <= 2)::numeric
            / NULLIF(COUNT(*), 0),
        CASE WHEN COUNT(*) >= p_minimum_denominator
            THEN 1::numeric ELSE 0::numeric END AS coverage
    FROM retail.order_reviews AS reviews
    JOIN retail.orders AS orders ON orders.order_id = reviews.order_id
    WHERE reviews.review_creation_date >= p_started_at
      AND reviews.review_creation_date < p_ended_at
      AND orders.order_status = ANY(p_status_filters)
    GROUP BY window_start
    ORDER BY window_start;
END;""",
        )
    )


def _create_audit_function() -> None:
    op.execute(
        _security_definer_function(
            "append_audit_event",
            """    p_event_id uuid,
    p_command_execution_id uuid,
    p_event_type text,
    p_schema_version integer,
    p_proposal_id uuid,
    p_proposal_version integer,
    p_actor_id uuid,
    p_command_type text,
    p_payload_sha256 text,
    p_public_target_refs jsonb,
    p_before_version integer,
    p_after_version integer,
    p_result_code text,
    p_occurred_at timestamptz,
    p_scenario_id text""",
            "uuid",
            """DECLARE
    v_execution ops.command_execution%ROWTYPE;
BEGIN
    SELECT execution.*
      INTO v_execution
      FROM ops.command_execution AS execution
     WHERE execution.execution_id = p_command_execution_id
       AND execution.proposal_id = p_proposal_id
       AND execution.proposal_version = p_proposal_version
       AND execution.actor_id = p_actor_id
       AND execution.command_type = p_command_type
       AND execution.status = 'succeeded'
       AND execution.before_version = p_before_version
       AND execution.after_version = p_after_version
       AND execution.result_code = p_result_code
       AND execution.scenario_id = p_scenario_id;
    IF NOT FOUND OR p_event_type <> 'operation_executed'
       OR NOT EXISTS (
           SELECT 1
             FROM ops.operation_proposal AS proposal
            WHERE proposal.proposal_id = p_proposal_id
              AND proposal.proposal_version = p_proposal_version
              AND proposal.payload_sha256 = p_payload_sha256
              AND proposal.scenario_id = p_scenario_id
       ) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'audit_binding_invalid';
    END IF;

    INSERT INTO ops.audit_event (
        event_id, command_execution_id, event_type, schema_version, proposal_id,
        proposal_version, actor_id, command_type, payload_sha256,
        public_target_refs, before_version, after_version, result_code,
        occurred_at, scenario_id
    ) VALUES (
        p_event_id, p_command_execution_id, p_event_type, p_schema_version,
        p_proposal_id, p_proposal_version, p_actor_id, p_command_type,
        p_payload_sha256, p_public_target_refs, p_before_version,
        p_after_version, p_result_code, p_occurred_at, p_scenario_id
    ) ON CONFLICT (command_execution_id, event_type) DO NOTHING;
    RETURN p_event_id;
END;""",
        )
    )


def _create_reset_function() -> None:
    op.execute(
        _security_definer_function(
            "reset_product_scenario",
            "    p_scenario_id text",
            "void",
            """BEGIN
    IF p_scenario_id !~ '^[a-z][a-z0-9-]{2,127}-v[0-9]+$' THEN
        RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
            MESSAGE = 'scenario_id_invalid';
    END IF;
    DELETE FROM ops.audit_event WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.risk_annotation WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.investigation_conclusion WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.metric_alert_rule WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.command_execution WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.approval_nonce WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.approval_decision WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.operation_proposal WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.alert_backtest_result WHERE scenario_id = p_scenario_id;
    DELETE FROM ops.investigation_task WHERE scenario_id = p_scenario_id;
    DELETE FROM app.product_trace_event WHERE scenario_id = p_scenario_id;
END;""",
        )
    )


def _create_read_views() -> None:
    op.execute(
        """CREATE VIEW ops_read.investigation_tasks WITH (security_barrier=true) AS
SELECT
    task.task_ref,
    task.status,
    task.assignee_ref,
    task.version,
    task.evidence_summary
FROM ops.investigation_task AS task"""
    )
    op.execute(
        """CREATE VIEW ops_read.risk_annotations WITH (security_barrier=true) AS
SELECT
    risk.risk_ref,
    risk.seller_ref,
    risk.status,
    risk.observed_from,
    risk.observed_to,
    risk.metric_ref,
    risk.metric_value
FROM ops.risk_annotation AS risk"""
    )
    op.execute(
        """CREATE VIEW ops_read.enabled_metric_alert_rules WITH (security_barrier=true) AS
SELECT
    rule.rule_ref,
    rule.metric_ref,
    rule.metric_revision,
    rule.grain,
    rule.comparator,
    rule.threshold,
    rule."window",
    rule.version
FROM ops.metric_alert_rule AS rule
WHERE rule.status = 'enabled'"""
    )
    op.execute(
        """CREATE VIEW ops_read.enabled_metric_alert_hits WITH (security_barrier=true) AS
SELECT
    md5(rule.rule_ref::text || ':' || (hit->>'window_started_at'))::uuid AS hit_ref,
    rule.rule_ref,
    (hit->>'window_started_at')::timestamptz AS window_start,
    (hit->>'window_ended_at')::timestamptz AS window_end,
    (hit->>'normalized_value')::numeric AS value,
    COALESCE((hit->>'coverage')::numeric, backtest.coverage) AS coverage
FROM ops.metric_alert_rule AS rule
JOIN ops.alert_backtest_result AS backtest
  ON backtest.backtest_ref = rule.backtest_ref
CROSS JOIN LATERAL jsonb_array_elements(backtest.window_results) AS hit
WHERE rule.status = 'enabled'
  AND COALESCE((hit->>'hit')::boolean, false)"""
    )


_FUNCTION_SIGNATURES = {
    "create_operation_proposal": (
        "uuid, integer, uuid, text, integer, jsonb, text, jsonb, jsonb, jsonb, "
        "text, timestamptz, text, text, text, text"
    ),
    "record_approval_decision": (
        "uuid, integer, uuid, uuid, text, text, text, text, text, integer, "
        "timestamptz, text, text"
    ),
    "store_alert_backtest": "uuid, text, text, jsonb, numeric, text, timestamptz",
    "find_seller_target_candidates": (
        "text, timestamptz, timestamptz, numeric, bigint, text[]"
    ),
    "read_late_delivery_backtest": (
        "timestamptz, timestamptz, text, bigint, text[]"
    ),
    "read_low_rating_backtest": (
        "timestamptz, timestamptz, text, bigint, text[]"
    ),
    "read_cancellation_backtest": (
        "timestamptz, timestamptz, text, bigint, text[]"
    ),
    "read_operation_proposal": "uuid, integer, text",
    "read_approval_decision": "uuid, integer, text",
    "append_audit_event": (
        "uuid, uuid, text, integer, uuid, integer, uuid, text, text, jsonb, "
        "integer, integer, text, timestamptz, text"
    ),
    "execute_open_seller_risk_case": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, uuid, "
        "text, text, text, timestamptz, timestamptz, text, text, bigint, bigint, "
        "numeric, numeric, text, text, jsonb"
    ),
    "execute_create_investigation_task": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, text, "
        "text, text, text"
    ),
    "execute_create_investigation_from_alert_hit": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, uuid, "
        "text, text, text"
    ),
    "execute_create_and_enable_metric_alert_rule": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, uuid, "
        "text, text, text, text, text, numeric, bigint, jsonb, text"
    ),
    "execute_assign_investigation": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, integer, text"
    ),
    "execute_transition_investigation": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, integer, "
        "text, text, text"
    ),
    "execute_add_investigation_conclusion": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, integer, "
        "text, text, text, jsonb"
    ),
    "execute_close_investigation": (
        "uuid, uuid, integer, uuid, text, text, text, text, text, uuid, integer, "
        "text, text"
    ),
    "reset_product_scenario": "text",
}


def _function_ref(name: str) -> str:
    return f"trusted_schema.{name}({_FUNCTION_SIGNATURES[name]})"


def _apply_ownership_and_acl() -> None:
    for table_name in (
        "operation_proposal",
        "approval_decision",
        "approval_nonce",
        "command_execution",
        "investigation_task",
        "investigation_conclusion",
        "risk_annotation",
        "metric_alert_rule",
        "alert_backtest_result",
    ):
        op.execute(f"ALTER TABLE ops.{table_name} OWNER TO ops_owner")
    op.execute("ALTER TABLE ops.audit_event OWNER TO audit_owner")
    op.execute("ALTER TABLE app.product_trace_event OWNER TO ops_owner")
    for view_name in (
        "investigation_tasks",
        "risk_annotations",
        "enabled_metric_alert_rules",
        "enabled_metric_alert_hits",
    ):
        op.execute(f"ALTER VIEW ops_read.{view_name} OWNER TO ops_owner")

    for name in (
        "create_operation_proposal",
        "record_approval_decision",
        "store_alert_backtest",
        "read_operation_proposal",
        "read_approval_decision",
        "execute_open_seller_risk_case",
        "execute_create_investigation_task",
        "execute_create_investigation_from_alert_hit",
        "execute_create_and_enable_metric_alert_rule",
        "execute_assign_investigation",
        "execute_transition_investigation",
        "execute_add_investigation_conclusion",
        "execute_close_investigation",
    ):
        op.execute(f"ALTER FUNCTION {_function_ref(name)} OWNER TO ops_owner")
    for name in (
        "find_seller_target_candidates",
        "read_late_delivery_backtest",
        "read_low_rating_backtest",
        "read_cancellation_backtest",
    ):
        op.execute(f"ALTER FUNCTION {_function_ref(name)} OWNER TO retail_owner")
    op.execute(
        f"ALTER FUNCTION {_function_ref('append_audit_event')} OWNER TO audit_owner"
    )
    op.execute(
        f"ALTER FUNCTION {_function_ref('reset_product_scenario')} "
        "OWNER TO scenario_reset_owner"
    )

    op.execute("REVOKE ALL ON SCHEMA ops FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA trusted_schema FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA ops FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE app.product_trace_event FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA trusted_schema FROM PUBLIC")
    for view_name in (
        "investigation_tasks",
        "risk_annotations",
        "enabled_metric_alert_rules",
        "enabled_metric_alert_hits",
    ):
        op.execute(f"REVOKE ALL ON ops_read.{view_name} FROM PUBLIC")
    op.execute("REVOKE ALL ON ops.audit_event FROM proposal_writer")
    op.execute("REVOKE ALL ON ops.audit_event FROM approval_writer")
    op.execute("REVOKE ALL ON ops.audit_event FROM operation_executor")
    op.execute(
        "REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM "
        "proposal_writer, approval_writer, operation_executor, agent_reader, trace_writer"
    )

    op.execute(
        "GRANT USAGE ON SCHEMA trusted_schema TO proposal_writer, approval_writer, "
        "operation_executor, retail_owner, ops_owner, audit_owner, scenario_reset_owner"
    )
    op.execute("GRANT USAGE ON SCHEMA trusted_schema TO product_scenario_reset")
    op.execute(
        "GRANT USAGE ON SCHEMA ops TO operation_executor, audit_owner, "
        "scenario_reset_owner"
    )
    op.execute("GRANT USAGE ON SCHEMA app TO trace_writer, scenario_reset_owner")
    op.execute("GRANT USAGE ON SCHEMA ops_read TO agent_reader")
    op.execute(
        "GRANT SELECT ON ops.operation_proposal, ops.command_execution TO audit_owner"
    )
    op.execute(
        "GRANT SELECT (scenario_id) ON ops.operation_proposal, "
        "ops.approval_decision, ops.approval_nonce, ops.command_execution, "
        "ops.investigation_task, ops.investigation_conclusion, "
        "ops.risk_annotation, ops.metric_alert_rule, ops.alert_backtest_result, "
        "ops.audit_event TO scenario_reset_owner"
    )
    op.execute(
        "GRANT DELETE ON ops.operation_proposal, ops.approval_decision, "
        "ops.approval_nonce, ops.command_execution, ops.investigation_task, "
        "ops.investigation_conclusion, ops.risk_annotation, ops.metric_alert_rule, "
        "ops.alert_backtest_result, ops.audit_event TO scenario_reset_owner"
    )
    op.execute(
        "GRANT SELECT (scenario_id) ON app.product_trace_event "
        "TO scenario_reset_owner"
    )
    op.execute(
        "GRANT DELETE ON app.product_trace_event TO scenario_reset_owner"
    )
    op.execute("GRANT INSERT ON app.product_trace_event TO trace_writer")
    op.execute(
        "GRANT SELECT (execution_id, proposal_id, proposal_version, command_type, "
        "status, before_version, after_version, result_code, public_summary, "
        "audit_ref, committed_at) ON ops.command_execution TO operation_executor"
    )
    for view_name in (
        "investigation_tasks",
        "risk_annotations",
        "enabled_metric_alert_rules",
        "enabled_metric_alert_hits",
    ):
        op.execute(f"GRANT SELECT ON ops_read.{view_name} TO agent_reader")

    for name in (
        "create_operation_proposal",
        "store_alert_backtest",
        "find_seller_target_candidates",
        "read_late_delivery_backtest",
        "read_low_rating_backtest",
        "read_cancellation_backtest",
        "read_operation_proposal",
    ):
        op.execute(
            f"GRANT EXECUTE ON FUNCTION {_function_ref(name)} TO proposal_writer"
        )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"{_function_ref('record_approval_decision')} TO approval_writer"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"{_function_ref('read_approval_decision')} TO approval_writer"
    )
    for name in (
        "execute_open_seller_risk_case",
        "execute_create_investigation_task",
        "execute_create_investigation_from_alert_hit",
        "execute_create_and_enable_metric_alert_rule",
        "execute_assign_investigation",
        "execute_transition_investigation",
        "execute_add_investigation_conclusion",
        "execute_close_investigation",
    ):
        op.execute(
            f"GRANT EXECUTE ON FUNCTION {_function_ref(name)} TO operation_executor"
        )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"{_function_ref('append_audit_event')} TO operation_executor, ops_owner"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        f"{_function_ref('reset_product_scenario')} TO product_scenario_reset"
    )


def _create_command_functions() -> None:
    op.execute(
        _command_function(
            "execute_open_seller_risk_case",
            "open_seller_risk_case",
            """    p_task_ref uuid,
    p_risk_ref uuid,
    p_seller_id text,
    p_seller_digest text,
    p_seller_ref text,
    p_observed_from timestamptz,
    p_observed_to timestamptz,
    p_metric_ref text,
    p_metric_revision text,
    p_numerator bigint,
    p_denominator bigint,
    p_metric_value numeric,
    p_threshold numeric,
    p_title text,
    p_priority text,
    p_evidence_refs jsonb""",
            """    IF v_proposal.target_versions <>
       '{"investigation_task": 0, "risk_annotation": 0}'::jsonb
       OR EXISTS (SELECT 1 FROM ops.investigation_task WHERE task_ref = p_task_ref)
       OR EXISTS (SELECT 1 FROM ops.risk_annotation WHERE risk_ref = p_risk_ref) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'target_version_conflict';
    END IF;
    INSERT INTO ops.investigation_task (
        task_ref, status, priority, public_summary, evidence_summary, version,
        scenario_id, created_at, updated_at
    ) VALUES (
        p_task_ref, 'open', p_priority, 'Seller risk requires investigation.',
        'Evidence-bound seller risk observation.', 1, p_scenario_id,
        transaction_timestamp(), transaction_timestamp()
    );
    INSERT INTO ops.risk_annotation (
        risk_ref, seller_id, seller_digest, seller_ref, status, observed_from, observed_to,
        metric_ref, metric_revision, numerator, denominator, metric_value,
        threshold, evidence_refs, task_ref, version, scenario_id, created_at,
        updated_at
    ) VALUES (
        p_risk_ref, p_seller_id, p_seller_digest, p_seller_ref, 'under_investigation',
        p_observed_from, p_observed_to, p_metric_ref, p_metric_revision,
        p_numerator, p_denominator, p_metric_value, p_threshold,
        p_evidence_refs, p_task_ref, 1, p_scenario_id,
        transaction_timestamp(), transaction_timestamp()
    );
    v_before_version := 0;
    v_after_version := 1;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref),
        jsonb_build_object('risk_ref', p_risk_ref)
    );
    v_public_summary := 'Created a seller-risk annotation and investigation task.';""",
        )
    )

    op.execute(
        _command_function(
            "execute_create_investigation_task",
            "create_investigation_task",
            """    p_task_ref uuid,
    p_title text,
    p_priority text,
    p_public_summary text,
    p_evidence_summary text""",
            """    IF v_proposal.target_versions <> '{"investigation_task": 0}'::jsonb
       OR EXISTS (SELECT 1 FROM ops.investigation_task WHERE task_ref = p_task_ref) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'target_version_conflict';
    END IF;
    INSERT INTO ops.investigation_task (
        task_ref, status, priority, public_summary, evidence_summary, version,
        scenario_id, created_at, updated_at
    ) VALUES (
        p_task_ref, 'open', p_priority, p_public_summary, p_evidence_summary, 1,
        p_scenario_id, transaction_timestamp(), transaction_timestamp()
    );
    v_before_version := 0;
    v_after_version := 1;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref)
    );
    v_public_summary := 'Created one investigation task.';""",
        )
    )

    op.execute(
        _command_function(
            "execute_create_investigation_from_alert_hit",
            "create_investigation_from_alert_hit",
            """    p_task_ref uuid,
    p_alert_hit_ref uuid,
    p_title text,
    p_priority text,
    p_evidence_summary text""",
            """    IF v_proposal.target_versions <> '{"investigation_task": 0}'::jsonb
       OR EXISTS (SELECT 1 FROM ops.investigation_task WHERE task_ref = p_task_ref)
       OR NOT EXISTS (
           SELECT 1
             FROM ops.metric_alert_rule AS rule
             JOIN ops.alert_backtest_result AS backtest
               ON backtest.backtest_ref = rule.backtest_ref
             CROSS JOIN LATERAL jsonb_array_elements(backtest.window_results) AS hit
            WHERE rule.status = 'enabled'
              AND rule.scenario_id = p_scenario_id
              AND md5(rule.rule_ref::text || ':' ||
                  (hit->>'window_started_at'))::uuid = p_alert_hit_ref
              AND COALESCE((hit->>'hit')::boolean, false)
       ) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'alert_hit_binding_invalid';
    END IF;
    INSERT INTO ops.investigation_task (
        task_ref, status, priority, public_summary, evidence_summary, version,
        scenario_id, created_at, updated_at
    ) VALUES (
        p_task_ref, 'open', p_priority, 'Created from a reviewed alert hit.',
        p_evidence_summary, 1, p_scenario_id,
        transaction_timestamp(), transaction_timestamp()
    );
    v_before_version := 0;
    v_after_version := 1;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref),
        jsonb_build_object('hit_ref', p_alert_hit_ref)
    );
    v_public_summary := 'Created one investigation task from an enabled alert hit.';""",
        )
    )

    op.execute(
        _command_function(
            "execute_create_and_enable_metric_alert_rule",
            "create_and_enable_metric_alert_rule",
            """    p_rule_ref uuid,
    p_backtest_ref uuid,
    p_metric_ref text,
    p_metric_revision text,
    p_grain text,
    p_window text,
    p_comparator text,
    p_threshold numeric,
    p_minimum_denominator bigint,
    p_filter_refs jsonb,
    p_rule_spec_sha256 text""",
            """    IF v_proposal.target_versions <> '{"metric_alert_rule": 0}'::jsonb
       OR EXISTS (SELECT 1 FROM ops.metric_alert_rule WHERE rule_ref = p_rule_ref)
       OR NOT EXISTS (
           SELECT 1
             FROM ops.alert_backtest_result AS backtest
            WHERE backtest.backtest_ref = p_backtest_ref
              AND backtest.rule_spec_sha256 = p_rule_spec_sha256
              AND backtest.scenario_id = p_scenario_id
            FOR UPDATE
       ) THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'backtest_binding_invalid';
    END IF;
    INSERT INTO ops.metric_alert_rule (
        rule_ref, status, metric_ref, metric_revision, grain, "window", comparator,
        threshold, minimum_denominator, filter_refs, rule_spec_sha256,
        backtest_ref, version, scenario_id, created_at, updated_at
    ) VALUES (
        p_rule_ref, 'enabled', p_metric_ref, p_metric_revision, p_grain,
        p_window, p_comparator, p_threshold, p_minimum_denominator,
        p_filter_refs, p_rule_spec_sha256, p_backtest_ref, 1, p_scenario_id,
        transaction_timestamp(), transaction_timestamp()
    );
    v_before_version := 0;
    v_after_version := 1;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('rule_ref', p_rule_ref)
    );
    v_public_summary := 'Created one enabled metric alert rule.';""",
        )
    )

    op.execute(
        _command_function(
            "execute_assign_investigation",
            "assign_investigation",
            """    p_task_ref uuid,
    p_expected_version integer,
    p_assignee_ref text""",
            """    SELECT task.version
      INTO v_before_version
      FROM ops.investigation_task AS task
     WHERE task.task_ref = p_task_ref
       AND task.scenario_id = p_scenario_id
     FOR UPDATE;
    IF NOT FOUND OR v_before_version <> p_expected_version
       OR v_proposal.target_versions <>
          jsonb_build_object('investigation_task', p_expected_version) THEN
        RAISE EXCEPTION USING ERRCODE = 'serialization_failure',
            MESSAGE = 'target_version_conflict';
    END IF;
    v_after_version := v_before_version + 1;
    UPDATE ops.investigation_task
       SET assignee_ref = p_assignee_ref,
           version = v_after_version,
           updated_at = transaction_timestamp()
     WHERE task_ref = p_task_ref;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref)
    );
    v_public_summary := 'Assigned the investigation task.';""",
        )
    )

    op.execute(
        _command_function(
            "execute_transition_investigation",
            "transition_investigation",
            """    p_task_ref uuid,
    p_expected_version integer,
    p_from_status text,
    p_to_status text,
    p_reason_code text""",
            """    SELECT task.version
      INTO v_before_version
      FROM ops.investigation_task AS task
     WHERE task.task_ref = p_task_ref
       AND task.status = p_from_status
       AND task.scenario_id = p_scenario_id
     FOR UPDATE;
    IF NOT FOUND OR v_before_version <> p_expected_version
       OR v_proposal.target_versions <>
          jsonb_build_object('investigation_task', p_expected_version)
       OR (p_from_status, p_to_status) NOT IN (
           ('open', 'in_progress'), ('open', 'blocked'),
           ('in_progress', 'blocked'), ('in_progress', 'resolved'),
           ('blocked', 'in_progress'), ('blocked', 'resolved')
       ) THEN
        RAISE EXCEPTION USING ERRCODE = 'serialization_failure',
            MESSAGE = 'investigation_transition_invalid';
    END IF;
    v_after_version := v_before_version + 1;
    UPDATE ops.investigation_task
       SET status = p_to_status,
           version = v_after_version,
           updated_at = transaction_timestamp()
     WHERE task_ref = p_task_ref;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref)
    );
    v_public_summary := 'Transitioned the investigation task: ' || p_reason_code;""",
        )
    )

    op.execute(
        _command_function(
            "execute_add_investigation_conclusion",
            "add_investigation_conclusion",
            """    p_task_ref uuid,
    p_expected_version integer,
    p_conclusion_ref text,
    p_conclusion_code text,
    p_conclusion_summary text,
    p_evidence_refs jsonb""",
            """    SELECT task.version
      INTO v_before_version
      FROM ops.investigation_task AS task
     WHERE task.task_ref = p_task_ref
       AND task.status = 'resolved'
       AND task.scenario_id = p_scenario_id
     FOR UPDATE;
    IF NOT FOUND OR v_before_version <> p_expected_version
       OR v_proposal.target_versions <>
          jsonb_build_object('investigation_task', p_expected_version) THEN
        RAISE EXCEPTION USING ERRCODE = 'serialization_failure',
            MESSAGE = 'target_version_conflict';
    END IF;
    v_after_version := v_before_version + 1;
    INSERT INTO ops.investigation_conclusion (
        conclusion_ref, task_ref, conclusion_code, public_summary,
        evidence_refs, task_version, scenario_id, created_at
    ) VALUES (
        p_conclusion_ref, p_task_ref, p_conclusion_code, p_conclusion_summary,
        p_evidence_refs, v_after_version, p_scenario_id, transaction_timestamp()
    );
    UPDATE ops.investigation_task
       SET version = v_after_version, updated_at = transaction_timestamp()
     WHERE task_ref = p_task_ref;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref),
        jsonb_build_object('conclusion_ref', p_conclusion_ref)
    );
    v_public_summary := 'Added the investigation conclusion.';""",
        )
    )

    op.execute(
        _command_function(
            "execute_close_investigation",
            "close_investigation",
            """    p_task_ref uuid,
    p_expected_version integer,
    p_conclusion_ref text,
    p_risk_disposition text""",
            """    SELECT task.version
      INTO v_before_version
      FROM ops.investigation_task AS task
     WHERE task.task_ref = p_task_ref
       AND task.status = 'resolved'
       AND task.scenario_id = p_scenario_id
     FOR UPDATE;
    IF NOT FOUND OR v_before_version <> p_expected_version
       OR v_proposal.target_versions <>
          jsonb_build_object('investigation_task', p_expected_version)
       OR NOT EXISTS (
           SELECT 1 FROM ops.investigation_conclusion AS conclusion
            WHERE conclusion.conclusion_ref = p_conclusion_ref
              AND conclusion.task_ref = p_task_ref
              AND conclusion.scenario_id = p_scenario_id
       ) THEN
        RAISE EXCEPTION USING ERRCODE = 'serialization_failure',
            MESSAGE = 'close_investigation_invalid';
    END IF;
    IF EXISTS (
        SELECT 1 FROM ops.risk_annotation AS risk
         WHERE risk.task_ref = p_task_ref
         FOR UPDATE
    ) THEN
        IF p_risk_disposition NOT IN ('confirmed', 'dismissed', 'inconclusive') THEN
            RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
                MESSAGE = 'risk_disposition_required';
        END IF;
        UPDATE ops.risk_annotation
           SET status = p_risk_disposition,
               version = version + 1,
               updated_at = transaction_timestamp()
         WHERE task_ref = p_task_ref;
    ELSIF p_risk_disposition IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'invalid_parameter_value',
            MESSAGE = 'risk_disposition_forbidden';
    END IF;
    v_after_version := v_before_version + 1;
    UPDATE ops.investigation_task
       SET status = 'closed', version = v_after_version,
           updated_at = transaction_timestamp()
     WHERE task_ref = p_task_ref;
    v_public_target_refs := jsonb_build_array(
        jsonb_build_object('task_ref', p_task_ref),
        jsonb_build_object('conclusion_ref', p_conclusion_ref)
    );
    v_public_summary := 'Closed the investigation task.';""",
        )
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA ops AUTHORIZATION ops_owner")
    op.execute("CREATE SCHEMA trusted_schema AUTHORIZATION ops_owner")
    op.execute("CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION ops_owner")

    op.create_table(
        "operation_proposal",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("command_schema_version", sa.Integer(), nullable=False),
        sa.Column("canonical_payload", _jsonb(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("evidence_refs", _jsonb(), nullable=False),
        sa.Column("preview", _jsonb(), nullable=False),
        sa.Column("target_versions", _jsonb(), nullable=False),
        sa.Column("target_versions_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", _timestamp(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("private_seller_id", sa.Text()),
        sa.Column("private_evidence_digest", sa.Text()),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            _timestamp(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            _timestamp(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "proposal_id", "proposal_version", name="pk_operation_proposal"
        ),
        sa.UniqueConstraint(
            "requester_id",
            "idempotency_key",
            name="uq_operation_proposal_requester_idempotency",
        ),
        sa.CheckConstraint(
            "proposal_version > 0", name="ck_operation_proposal_version"
        ),
        sa.CheckConstraint(
            "command_schema_version > 0",
            name="ck_operation_proposal_command_schema_version",
        ),
        sa.CheckConstraint(
            f"command_type IN ({_COMMAND_TYPES})",
            name="ck_operation_proposal_command_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_PROPOSAL_STATUSES})",
            name="ck_operation_proposal_status",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_operation_proposal_payload_sha256",
        ),
        sa.CheckConstraint(
            "target_versions_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_operation_proposal_target_versions_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(canonical_payload) = 'object'",
            name="ck_operation_proposal_payload_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) > 0",
            name="ck_operation_proposal_evidence_refs",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preview) = 'object' AND jsonb_typeof(target_versions) = 'object'",
            name="ck_operation_proposal_preview_targets",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 128",
            name="ck_operation_proposal_idempotency_key",
        ),
        sa.CheckConstraint(
            "(command_type = 'open_seller_risk_case' "
            "AND private_seller_id IS NOT NULL "
            "AND private_evidence_digest ~ '^[0-9a-f]{64}$') OR "
            "(command_type <> 'open_seller_risk_case' "
            "AND private_seller_id IS NULL AND private_evidence_digest IS NULL)",
            name="ck_operation_proposal_private_binding",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_operation_proposal_expiry"
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_operation_proposal_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "approval_decision",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_at", _timestamp(), nullable=False),
        sa.Column("grant_sha256", sa.Text()),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "proposal_id", "proposal_version", name="pk_approval_decision"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "proposal_version"],
            ["ops.operation_proposal.proposal_id", "ops.operation_proposal.proposal_version"],
            name="fk_approval_decision_proposal",
        ),
        sa.CheckConstraint(
            "decision IN ('approve','reject')", name="ck_approval_decision_value"
        ),
        sa.CheckConstraint(
            "requester_id <> approver_id", name="ck_approval_decision_actor_separation"
        ),
        sa.CheckConstraint(
            "decision = 'approve' OR length(reason) BETWEEN 1 AND 1000",
            name="ck_approval_decision_rejection_reason",
        ),
        sa.CheckConstraint(
            "(decision = 'approve' AND grant_sha256 ~ '^[0-9a-f]{64}$') OR "
            "(decision = 'reject' AND grant_sha256 IS NULL)",
            name="ck_approval_decision_grant_digest",
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_approval_decision_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "approval_nonce",
        sa.Column("nonce_digest", sa.Text(), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("target_versions_sha256", sa.Text(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", _timestamp(), nullable=False),
        sa.Column("used_at", _timestamp()),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("nonce_digest", name="pk_approval_nonce"),
        sa.UniqueConstraint(
            "proposal_id", "proposal_version", name="uq_approval_nonce_proposal"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "proposal_version"],
            ["ops.approval_decision.proposal_id", "ops.approval_decision.proposal_version"],
            name="fk_approval_nonce_decision",
        ),
        sa.CheckConstraint(
            "nonce_digest ~ '^[0-9a-f]{64}$'", name="ck_approval_nonce_digest"
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_approval_nonce_payload_sha256",
        ),
        sa.CheckConstraint(
            "target_versions_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_approval_nonce_target_versions_sha256",
        ),
        sa.CheckConstraint("key_version > 0", name="ck_approval_nonce_key_version"),
        sa.CheckConstraint(
            "used_at IS NULL OR used_at < expires_at", name="ck_approval_nonce_use_time"
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_approval_nonce_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "command_execution",
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("before_version", sa.Integer()),
        sa.Column("after_version", sa.Integer()),
        sa.Column("result_code", sa.Text()),
        sa.Column("public_summary", sa.Text()),
        sa.Column("audit_ref", sa.Text()),
        sa.Column("started_at", _timestamp(), nullable=False),
        sa.Column("committed_at", _timestamp()),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("execution_id", name="pk_command_execution"),
        sa.UniqueConstraint(
            "proposal_id", "proposal_version", name="uq_command_execution_proposal"
        ),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_command_execution_actor_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "proposal_version"],
            ["ops.operation_proposal.proposal_id", "ops.operation_proposal.proposal_version"],
            name="fk_command_execution_proposal",
        ),
        sa.CheckConstraint(
            f"command_type IN ({_COMMAND_TYPES})", name="ck_command_execution_command_type"
        ),
        sa.CheckConstraint(
            f"status IN ({_EXECUTION_STATUSES})", name="ck_command_execution_status"
        ),
        sa.CheckConstraint(
            "before_version IS NULL OR before_version >= 0",
            name="ck_command_execution_before_version",
        ),
        sa.CheckConstraint(
            "after_version IS NULL OR after_version > before_version",
            name="ck_command_execution_after_version",
        ),
        sa.CheckConstraint(
            "(status = 'not_started' AND committed_at IS NULL) OR "
            "(status <> 'not_started' AND committed_at IS NOT NULL)",
            name="ck_command_execution_terminal_time",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 128",
            name="ck_command_execution_idempotency_key",
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_command_execution_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "investigation_task",
        sa.Column("task_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("assignee_ref", sa.Text()),
        sa.Column("public_summary", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("created_at", _timestamp(), nullable=False),
        sa.Column("updated_at", _timestamp(), nullable=False),
        sa.PrimaryKeyConstraint("task_ref", name="pk_investigation_task"),
        sa.CheckConstraint(
            f"status IN ({_INVESTIGATION_STATUSES})",
            name="ck_investigation_task_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','medium','high')",
            name="ck_investigation_task_priority",
        ),
        sa.CheckConstraint("version > 0", name="ck_investigation_task_version"),
        sa.CheckConstraint(
            "length(public_summary) BETWEEN 1 AND 2000",
            name="ck_investigation_task_public_summary",
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_investigation_task_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "investigation_conclusion",
        sa.Column("conclusion_ref", sa.Text(), nullable=False),
        sa.Column("task_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conclusion_code", sa.Text(), nullable=False),
        sa.Column("public_summary", sa.Text(), nullable=False),
        sa.Column("evidence_refs", _jsonb(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("created_at", _timestamp(), nullable=False),
        sa.PrimaryKeyConstraint("conclusion_ref", name="pk_investigation_conclusion"),
        sa.UniqueConstraint(
            "task_ref", "task_version", name="uq_investigation_conclusion_task_version"
        ),
        sa.ForeignKeyConstraint(
            ["task_ref"],
            ["ops.investigation_task.task_ref"],
            name="fk_investigation_conclusion_task",
        ),
        sa.CheckConstraint(
            "task_version > 0", name="ck_investigation_conclusion_task_version"
        ),
        sa.CheckConstraint(
            "conclusion_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_investigation_conclusion_code",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) > 0",
            name="ck_investigation_conclusion_evidence_refs",
        ),
        sa.CheckConstraint(
            _SCENARIO_CHECK, name="ck_investigation_conclusion_scenario_id"
        ),
        schema="ops",
    )

    op.create_table(
        "risk_annotation",
        sa.Column("risk_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", sa.Text(), nullable=False),
        sa.Column("seller_digest", sa.Text(), nullable=False),
        sa.Column("seller_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("observed_from", _timestamp(), nullable=False),
        sa.Column("observed_to", _timestamp(), nullable=False),
        sa.Column("metric_ref", sa.Text(), nullable=False),
        sa.Column("metric_revision", sa.Text(), nullable=False),
        sa.Column("numerator", sa.BigInteger(), nullable=False),
        sa.Column("denominator", sa.BigInteger(), nullable=False),
        sa.Column("metric_value", sa.Numeric(9, 6), nullable=False),
        sa.Column("threshold", sa.Numeric(9, 6), nullable=False),
        sa.Column("evidence_refs", _jsonb(), nullable=False),
        sa.Column("task_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("created_at", _timestamp(), nullable=False),
        sa.Column("updated_at", _timestamp(), nullable=False),
        sa.PrimaryKeyConstraint("risk_ref", name="pk_risk_annotation"),
        sa.UniqueConstraint("task_ref", name="uq_risk_annotation_task"),
        sa.ForeignKeyConstraint(
            ["task_ref"],
            ["ops.investigation_task.task_ref"],
            name="fk_risk_annotation_task",
        ),
        sa.CheckConstraint(
            f"status IN ({_RISK_STATUSES})", name="ck_risk_annotation_status"
        ),
        sa.CheckConstraint(
            "seller_digest ~ '^[0-9a-f]{64}$'",
            name="ck_risk_annotation_seller_digest",
        ),
        sa.CheckConstraint(
            "observed_from < observed_to", name="ck_risk_annotation_observation_range"
        ),
        sa.CheckConstraint(
            "numerator >= 0 AND denominator > 0 AND numerator <= denominator",
            name="ck_risk_annotation_counts",
        ),
        sa.CheckConstraint(
            "metric_value BETWEEN 0 AND 1 AND threshold BETWEEN 0 AND 1",
            name="ck_risk_annotation_ratios",
        ),
        sa.CheckConstraint("version > 0", name="ck_risk_annotation_version"),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' AND jsonb_array_length(evidence_refs) > 0",
            name="ck_risk_annotation_evidence_refs",
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_risk_annotation_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "alert_backtest_result",
        sa.Column("backtest_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_spec_sha256", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.Text(), nullable=False),
        sa.Column("window_results", _jsonb(), nullable=False),
        sa.Column("coverage", sa.Numeric(9, 6), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("created_at", _timestamp(), nullable=False),
        sa.PrimaryKeyConstraint("backtest_ref", name="pk_alert_backtest_result"),
        sa.UniqueConstraint(
            "rule_spec_sha256", "scenario_id", name="uq_alert_backtest_spec_scenario"
        ),
        sa.CheckConstraint(
            "rule_spec_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_alert_backtest_rule_spec_sha256",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_alert_backtest_evidence_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(window_results) = 'array'",
            name="ck_alert_backtest_window_results",
        ),
        sa.CheckConstraint(
            "coverage BETWEEN 0 AND 1", name="ck_alert_backtest_coverage"
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_alert_backtest_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "metric_alert_rule",
        sa.Column("rule_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metric_ref", sa.Text(), nullable=False),
        sa.Column("metric_revision", sa.Text(), nullable=False),
        sa.Column("grain", sa.Text(), nullable=False),
        sa.Column("window", sa.Text(), nullable=False),
        sa.Column("comparator", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Numeric(9, 6), nullable=False),
        sa.Column("minimum_denominator", sa.BigInteger(), nullable=False),
        sa.Column("filter_refs", _jsonb(), nullable=False),
        sa.Column("rule_spec_sha256", sa.Text(), nullable=False),
        sa.Column("backtest_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("created_at", _timestamp(), nullable=False),
        sa.Column("updated_at", _timestamp(), nullable=False),
        sa.PrimaryKeyConstraint("rule_ref", name="pk_metric_alert_rule"),
        sa.UniqueConstraint("backtest_ref", name="uq_metric_alert_rule_backtest"),
        sa.ForeignKeyConstraint(
            ["backtest_ref"],
            ["ops.alert_backtest_result.backtest_ref"],
            name="fk_metric_alert_rule_backtest",
        ),
        sa.CheckConstraint(
            "status = 'enabled'", name="ck_metric_alert_rule_enabled_only"
        ),
        sa.CheckConstraint(
            "grain ~ '^[a-z][a-z0-9_]{1,63}$'", name="ck_metric_alert_rule_grain"
        ),
        sa.CheckConstraint(
            '"window" IN (\'week\',\'month\')', name="ck_metric_alert_rule_window"
        ),
        sa.CheckConstraint(
            "comparator IN ('greater_than','greater_than_or_equal')",
            name="ck_metric_alert_rule_comparator",
        ),
        sa.CheckConstraint(
            "threshold BETWEEN 0 AND 1", name="ck_metric_alert_rule_threshold"
        ),
        sa.CheckConstraint(
            "minimum_denominator BETWEEN 1 AND 1000000000",
            name="ck_metric_alert_rule_minimum_denominator",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(filter_refs) = 'array'",
            name="ck_metric_alert_rule_filter_refs",
        ),
        sa.CheckConstraint(
            "rule_spec_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_metric_alert_rule_spec_sha256",
        ),
        sa.CheckConstraint("version > 0", name="ck_metric_alert_rule_version"),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_metric_alert_rule_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "audit_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("public_target_refs", _jsonb(), nullable=False),
        sa.Column("before_version", sa.Integer(), nullable=False),
        sa.Column("after_version", sa.Integer(), nullable=False),
        sa.Column("result_code", sa.Text(), nullable=False),
        sa.Column("occurred_at", _timestamp(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_event"),
        sa.UniqueConstraint(
            "command_execution_id", "event_type", name="uq_audit_event_execution_type"
        ),
        sa.ForeignKeyConstraint(
            ["command_execution_id"],
            ["ops.command_execution.execution_id"],
            name="fk_audit_event_execution",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "proposal_version"],
            ["ops.operation_proposal.proposal_id", "ops.operation_proposal.proposal_version"],
            name="fk_audit_event_proposal",
        ),
        sa.CheckConstraint(
            "event_type = 'operation_executed'", name="ck_audit_event_type"
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_audit_event_schema_version"),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_audit_event_payload_sha256"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(public_target_refs) = 'array'",
            name="ck_audit_event_public_target_refs",
        ),
        sa.CheckConstraint(
            "before_version >= 0 AND after_version > before_version",
            name="ck_audit_event_versions",
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_audit_event_scenario_id"),
        schema="ops",
    )

    op.create_table(
        "product_trace_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_scope_digest", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("node", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("safe_summary", _jsonb(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("prompt_policy_hash", sa.Text()),
        sa.Column("rendered_prompt_hash", sa.Text()),
        sa.Column("model_hash", sa.Text()),
        sa.Column("tool_hash", sa.Text()),
        sa.Column("context_hash", sa.Text()),
        sa.Column("reason_code", sa.Text()),
        sa.Column("occurred_at", _timestamp(), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_product_trace_event"),
        sa.UniqueConstraint(
            "run_scope_digest",
            "attempt_id",
            "sequence",
            name="uq_product_trace_scope_attempt_sequence",
        ),
        sa.CheckConstraint(
            "run_scope_digest ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_run_scope_digest",
        ),
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_config_hash",
        ),
        sa.CheckConstraint(
            "prompt_policy_hash IS NULL OR prompt_policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_prompt_policy_hash",
        ),
        sa.CheckConstraint(
            "rendered_prompt_hash IS NULL OR rendered_prompt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_rendered_prompt_hash",
        ),
        sa.CheckConstraint(
            "model_hash IS NULL OR model_hash ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_model_hash",
        ),
        sa.CheckConstraint(
            "tool_hash IS NULL OR tool_hash ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_tool_hash",
        ),
        sa.CheckConstraint(
            "context_hash IS NULL OR context_hash ~ '^[0-9a-f]{64}$'",
            name="ck_product_trace_context_hash",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_product_trace_sequence"),
        sa.CheckConstraint(
            "jsonb_typeof(safe_summary) = 'object'",
            name="ck_product_trace_safe_summary",
        ),
        sa.CheckConstraint(_SCENARIO_CHECK, name="ck_product_trace_scenario_id"),
        schema="app",
    )

    op.create_index(
        "ix_operation_proposal_requester_status_expiry",
        "operation_proposal",
        ["requester_id", "status", "expires_at"],
        schema="ops",
    )
    op.create_index(
        "uq_operation_proposal_one_live_version",
        "operation_proposal",
        ["proposal_id"],
        unique=True,
        schema="ops",
        postgresql_where=sa.text("status IN ('pending','approved')"),
    )
    op.create_index(
        "ix_operation_proposal_scenario_id",
        "operation_proposal",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_approval_decision_scenario_id",
        "approval_decision",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_approval_nonce_unused_expiry",
        "approval_nonce",
        ["expires_at"],
        schema="ops",
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.create_index(
        "ix_approval_nonce_scenario_id",
        "approval_nonce",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_command_execution_proposal_status",
        "command_execution",
        ["proposal_id", "proposal_version", "status"],
        schema="ops",
    )
    op.create_index(
        "ix_command_execution_scenario_id",
        "command_execution",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_investigation_task_status_assignee",
        "investigation_task",
        ["status", "assignee_ref"],
        schema="ops",
    )
    op.create_index(
        "ix_investigation_task_scenario_id",
        "investigation_task",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_investigation_conclusion_scenario_id",
        "investigation_conclusion",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_risk_annotation_seller_status",
        "risk_annotation",
        ["seller_digest", "status"],
        schema="ops",
    )
    op.create_index(
        "ix_risk_annotation_scenario_id",
        "risk_annotation",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_metric_alert_rule_status_spec",
        "metric_alert_rule",
        ["status", "rule_spec_sha256"],
        schema="ops",
    )
    op.create_index(
        "ix_metric_alert_rule_scenario_id",
        "metric_alert_rule",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_alert_backtest_result_scenario_id",
        "alert_backtest_result",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_audit_event_execution_time",
        "audit_event",
        ["command_execution_id", "occurred_at"],
        schema="ops",
    )
    op.create_index(
        "ix_audit_event_scenario_id",
        "audit_event",
        ["scenario_id"],
        schema="ops",
    )
    op.create_index(
        "ix_product_trace_scope_attempt_sequence",
        "product_trace_event",
        ["run_scope_digest", "attempt_id", "sequence"],
        schema="app",
    )
    op.create_index(
        "ix_product_trace_scenario_id",
        "product_trace_event",
        ["scenario_id"],
        schema="app",
    )

    _create_control_functions()
    _create_read_functions()
    _create_audit_function()
    _create_command_functions()
    _create_reset_function()
    _create_read_views()
    _apply_ownership_and_acl()


def downgrade() -> None:
    for view_name in (
        "enabled_metric_alert_hits",
        "enabled_metric_alert_rules",
        "risk_annotations",
        "investigation_tasks",
    ):
        op.execute(f"DROP VIEW IF EXISTS ops_read.{view_name}")

    for function_name in (
        "reset_product_scenario",
        "execute_close_investigation",
        "execute_add_investigation_conclusion",
        "execute_transition_investigation",
        "execute_assign_investigation",
        "execute_create_and_enable_metric_alert_rule",
        "execute_create_investigation_from_alert_hit",
        "execute_create_investigation_task",
        "execute_open_seller_risk_case",
        "append_audit_event",
        "read_approval_decision",
        "read_operation_proposal",
        "read_cancellation_backtest",
        "read_low_rating_backtest",
        "read_late_delivery_backtest",
        "find_seller_target_candidates",
        "store_alert_backtest",
        "record_approval_decision",
        "create_operation_proposal",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {_function_ref(function_name)}")

    for table_name in (
        "app.product_trace_event",
        "ops.audit_event",
        "ops.risk_annotation",
        "ops.investigation_conclusion",
        "ops.metric_alert_rule",
        "ops.command_execution",
        "ops.approval_nonce",
        "ops.approval_decision",
        "ops.operation_proposal",
        "ops.alert_backtest_result",
        "ops.investigation_task",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
    op.execute("DROP SCHEMA trusted_schema")
    op.execute("DROP SCHEMA ops")
