import hashlib
import json
from decimal import Decimal, InvalidOperation

from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine._postgres import PostgresExecutor
from commerce_agent.query_engine.contracts import (
    DecimalToleranceRule,
    EvidenceResult,
    GrainRule,
    NullRule,
    QueryRequest,
    QueryResult,
    ReconciliationCheck,
    ReconciliationRequest,
    ReconciliationResult,
    SetRule,
    TotalRule,
)
from commerce_agent.query_engine.errors import ReconciliationContractError


class QueryEngine:
    def __init__(self, policy: AstPolicy, executor: PostgresExecutor) -> None:
        self._policy = policy
        self._executor = executor

    async def execute(self, request: QueryRequest) -> QueryResult:
        validated = self._policy.validate(request.sql)
        return await self._executor.execute(validated)

    def reconcile(self, request: ReconciliationRequest) -> ReconciliationResult:
        indexed = {item.evidence_id: item for item in request.evidence}
        handlers = {
            "total": self._reconcile_total,
            "grain": self._reconcile_grain,
            "set": self._reconcile_set,
            "null": self._reconcile_null,
            "decimal_tolerance": self._reconcile_decimal,
        }
        checks = tuple(handlers[rule.type](rule, indexed) for rule in request.rules)
        return ReconciliationResult(
            passed=all(item.passed for item in checks), checks=checks
        )

    @staticmethod
    def _evidence(indexed: dict[str, EvidenceResult], reference: str) -> EvidenceResult:
        try:
            return indexed[reference]
        except KeyError as exc:
            raise ReconciliationContractError(
                "evidence_not_found", "Referenced evidence is absent"
            ) from exc

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _decimal_total(cls, evidence: EvidenceResult, column: str) -> Decimal:
        if column not in evidence.columns:
            raise ReconciliationContractError("column_missing", "Evidence column is absent")
        raw = [row[column] for row in evidence.rows]
        non_null_types = {type(item) for item in raw if item is not None}
        if len(non_null_types) > 1 or any(
            item is None or isinstance(item, bool | float) for item in raw
        ):
            raise ReconciliationContractError(
                "scalar_type_invalid", "Evidence scalar type is invalid"
            )
        try:
            values = tuple(Decimal(str(item)) for item in raw)
        except InvalidOperation as exc:
            raise ReconciliationContractError(
                "decimal_invalid", "Evidence decimal is invalid"
            ) from exc
        if any(not item.is_finite() for item in values):
            raise ReconciliationContractError(
                "decimal_not_finite", "Evidence decimal must be finite"
            )
        return sum(values, start=Decimal(0))

    @classmethod
    def _check(cls, rule_type: str, passed: bool, reason: str, actual, expected):
        return ReconciliationCheck(
            rule_type=rule_type,
            passed=passed,
            reason_code="reconciled" if passed else reason,
            actual_digest=cls._digest(actual),
            expected_digest=cls._digest(expected),
        )

    @classmethod
    def _reconcile_total(cls, rule: TotalRule, indexed):
        parent = cls._decimal_total(cls._evidence(indexed, rule.parent_ref), rule.column)
        children = cls._decimal_total(cls._evidence(indexed, rule.child_ref), rule.column)
        return cls._check("total", parent == children, "total_mismatch", str(children), str(parent))

    @classmethod
    def _reconcile_decimal(cls, rule: DecimalToleranceRule, indexed):
        left = cls._decimal_total(cls._evidence(indexed, rule.left_ref), rule.column)
        right = cls._decimal_total(cls._evidence(indexed, rule.right_ref), rule.column)
        difference = abs(left - right)
        return cls._check(
            "decimal_tolerance",
            difference <= rule.absolute_tolerance,
            "decimal_tolerance_failed",
            str(difference),
            str(rule.absolute_tolerance),
        )

    @classmethod
    def _reconcile_grain(cls, rule: GrainRule, indexed):
        evidence = cls._evidence(indexed, rule.evidence_ref)
        if any(column not in evidence.columns for column in rule.key_columns):
            raise ReconciliationContractError("column_missing", "Grain column is absent")
        keys = tuple(tuple(row[column] for column in rule.key_columns) for row in evidence.rows)
        passed = len(keys) == len(set(keys))
        return cls._check("grain", passed, "grain_not_unique", len(keys), len(set(keys)))

    @classmethod
    def _reconcile_set(cls, rule: SetRule, indexed):
        left_evidence = cls._evidence(indexed, rule.left_ref)
        right_evidence = cls._evidence(indexed, rule.right_ref)
        if any(
            column not in evidence.columns
            for evidence in (left_evidence, right_evidence)
            for column in rule.columns
        ):
            raise ReconciliationContractError("column_missing", "Set column is absent")
        left = {tuple(row[column] for column in rule.columns) for row in left_evidence.rows}
        right = {tuple(row[column] for column in rule.columns) for row in right_evidence.rows}
        return cls._check(
            "set", left == right, "set_mismatch", sorted(map(str, left)), sorted(map(str, right))
        )

    @classmethod
    def _reconcile_null(cls, rule: NullRule, indexed):
        evidence = cls._evidence(indexed, rule.evidence_ref)
        if any(column not in evidence.columns for column in rule.columns):
            raise ReconciliationContractError("column_missing", "Null column is absent")
        null_count = sum(
            row[column] is None for row in evidence.rows for column in rule.columns
        )
        passed = rule.allow_null or null_count == 0
        return cls._check("null", passed, "null_policy_failed", null_count, 0)
