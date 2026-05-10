#!/bin/bash
#
# Demo script to test the enhanced merge functionality
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[demo]${NC} $*"; }
log_ok() { echo -e "${GREEN}[demo]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[demo]${NC} $*"; }
log_error() { echo -e "${RED}[demo]${NC} $*" >&2; }

# Create a temporary test setup
DEMO_DIR=$(mktemp -d)
trap "rm -rf $DEMO_DIR" EXIT

log_info "Creating demo environment in $DEMO_DIR"

# Setup git repo
REPO_DIR="$DEMO_DIR/test_repo"
mkdir -p "$REPO_DIR"
cd "$REPO_DIR"

git init
git config user.name "Demo User"
git config user.email "demo@example.com"

# Initial commit
echo "# Demo Repo" > README.md
git add README.md
git commit -m "Initial commit"

log_ok "Created demo git repository"

# Create a mock task in ~/.rapper/tasks/ to test with rapper --merge
TASKS_DIR="$HOME/.rapper/tasks"
mkdir -p "$TASKS_DIR"

TASK_ID="demo-$(date +%Y%m%d-%H%M%S)"
BRANCH_NAME="rapper/demo-feature"
WORKTREE_PATH="$DEMO_DIR/worktree"

# Create worktree
git worktree add -b "$BRANCH_NAME" "$WORKTREE_PATH"
log_ok "Created worktree: $WORKTREE_PATH"

# Create mock task file that rapper --merge expects
cat > "$TASKS_DIR/$TASK_ID.json" <<EOF
{
  "id": "$TASK_ID",
  "name": "demo-feature",
  "status": "completed",
  "branch_name": "$BRANCH_NAME",
  "worktree_path": "$WORKTREE_PATH",
  "workdir": "$REPO_DIR",
  "repo_workdir": "$REPO_DIR"
}
EOF

log_ok "Created mock task file: $TASK_ID"

# Test Case 1: Worktree with files (should work)
log_info "=== Test Case 1: Worktree with files ==="
cd "$WORKTREE_PATH"
echo "def hello():" > feature.py
echo "    print('Hello from feature!')" >> feature.py
mkdir -p lib
echo "UTILS_VERSION = '1.0'" > lib/utils.py

log_info "Created files in worktree:"
ls -la
echo

# Test the enhanced merge
log_info "Testing enhanced merge with files..."
cd "$REPO_DIR"

# Run our enhanced merge function
log_info "Running: rapper --merge $TASK_ID"
if "$REPO_DIR/../../../rapper" --merge "$TASK_ID" 2>&1; then
    log_ok "Merge completed"

    log_info "Files in main repo after merge:"
    ls -la
    if [[ -f "feature.py" ]]; then
        log_ok "✅ feature.py merged successfully"
    else
        log_error "❌ feature.py not found"
    fi
    if [[ -f "lib/utils.py" ]]; then
        log_ok "✅ lib/utils.py merged successfully"
    else
        log_error "❌ lib/utils.py not found"
    fi
else
    log_error "Merge failed"
fi

echo
log_info "=== Test Case 2: Empty worktree (problematic case) ==="

# Create another worktree for testing empty case
BRANCH_NAME2="rapper/empty-feature"
WORKTREE_PATH2="$DEMO_DIR/worktree2"
TASK_ID2="empty-$(date +%Y%m%d-%H%M%S)"

git worktree add -b "$BRANCH_NAME2" "$WORKTREE_PATH2"

# Create mock task file for empty worktree test
cat > "$TASKS_DIR/$TASK_ID2.json" <<EOF
{
  "id": "$TASK_ID2",
  "name": "empty-feature",
  "status": "completed",
  "branch_name": "$BRANCH_NAME2",
  "worktree_path": "$WORKTREE_PATH2",
  "workdir": "$REPO_DIR",
  "repo_workdir": "$REPO_DIR"
}
EOF

log_info "Testing empty worktree (should show enhanced diagnostics)..."

# Don't add any files to this worktree - this simulates the reported bug
if "$REPO_DIR/../../../rapper" --merge "$TASK_ID2" 2>&1; then
    log_warn "Merge completed (but probably with 'Already up to date')"
else
    log_error "Merge failed"
fi

# Cleanup
rm -f "$TASKS_DIR/$TASK_ID.json" "$TASKS_DIR/$TASK_ID2.json"

log_ok "Demo completed!"
echo
log_info "Summary:"
log_info "- Case 1 (with files): Should merge successfully"
log_info "- Case 2 (empty): Should show enhanced diagnostics for debugging"
log_info "- The enhanced version provides detailed information about why merge fails"