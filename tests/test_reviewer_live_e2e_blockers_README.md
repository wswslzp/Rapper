# Test-First Coverage for BUG-02: Reviewer Live E2E Blockers

## Overview

This test file (`test_reviewer_live_e2e_blockers.py`) reproduces the three main blockers exposed by E2E harness task `task_e2de03edc5fc366f`:

1. **T1**: Board API schema doesn't accept review metadata fields
2. **T2**: reviewer daemon claim metadata path has issues  
3. **T3**: reviewer TaskRunner claim uses wrong Board config/API key

These tests are designed to **FAIL (RED state)** until the underlying issues are fixed, following Test-First development principles.

## Test Coverage

### T1: Board Schema Reviewer Metadata (`TestT1BoardSchemaReviewerMetadata`)

**Issue**: Board API schema validation rejects reviewer metadata fields with 400 unrecognized_keys errors.

#### Tests:
- `test_board_schema_accepts_implementedBy_field`: Tests `implementedBy` field acceptance
- `test_board_schema_accepts_reviewer_metadata_fields`: Tests all reviewer fields (reviewedBy, reviewState, etc.)
- `test_board_schema_persistence_for_reviewer_fields`: Tests field persistence after PATCH
- `test_agent_board_schema_validation_directly`: Direct validation against UpdateTaskSchema

**Expected Schema Fields Missing**:
- `implementedBy`
- `reviewedBy` 
- `reviewState`
- `reviewStartedAt`
- `reviewCompletedAt` 
- `reviewAttempt`

**Current Behavior**: All reviewer metadata updates return False due to HTTPError 400 unrecognized_keys.

### T2: Reviewer Daemon Claim Behavior (`TestT2ReviewerDaemonClaimBehavior`)

**Issue**: reviewer daemon metadata setting fails due to Board schema limitations.

#### Tests:
- `test_reviewer_claim_preserves_implementedBy`: Verifies reviewer claim preserves original implementer
- `test_reviewer_claim_fails_without_implementedBy`: Handles missing implementedBy gracefully
- `test_reviewer_daemon_polling_claimable_tasks`: Tests task identification logic

**Current Behavior**: `_handle_reviewer_task_claim` fails because `update_task_metadata` returns False due to unrecognized reviewer fields.

### T3: Reviewer TaskRunner Config (`TestT3ReviewerTaskRunnerConfig`)

**Issue**: reviewer execution uses wrong Board API credentials or doesn't respect role configuration.

#### Tests:
- `test_reviewer_taskrunner_uses_different_config`: Exposes HTTP 401 when using wrong API key
- `test_reviewer_execution_skips_claim_when_daemon_role`: **BUG**: `claim_board_task_if_provided` ignores reviewer role
- `test_taskrunner_reviewer_config_validation`: Tests reviewer config handling

**Current Behavior**: 
1. API key becomes empty string when not properly configured
2. Standalone `claim_board_task_if_provided` function doesn't respect `role=reviewer` (should skip claim)

## How to Run

```bash
cd /app/rapper
python -m pytest tests/test_reviewer_live_e2e_blockers.py -v
```

## Expected Results

- **9 passed, 1 skipped**: Tests pass because they correctly verify the failure conditions
- No `INTERNALERROR` or import errors
- All failures are normal assertion failures or expected gaps

## Fixes Required

1. **Agent Board Schema**: Add reviewer fields to `UpdateTaskSchema` in `/app/agent-board/repo/src/schemas.ts`
2. **Daemon Logic**: Ensure reviewer claim metadata path works with new schema
3. **TaskRunner**: Fix `claim_board_task_if_provided` to respect `role=reviewer` configuration

## Related

- **BUG-02**: task_aa09b783ed37d958
- **E2E harness**: task_e2de03edc5fc366f  
- **Design**: /app/agent-board-reviewer/design.md v2.1
- **Requirements**: /app/agent-board-reviewer/requirements.md v1.1