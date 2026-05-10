#!/bin/bash
#
# Simple test of the enhanced merge functionality
#
set -euo pipefail

RAPPER_DIR="/app/rapper"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[test]${NC} $*"; }
log_ok() { echo -e "${GREEN}[test]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[test]${NC} $*"; }
log_error() { echo -e "${RED}[test]${NC} $*" >&2; }

# Create test setup
DEMO_DIR=$(mktemp -d)
trap "rm -rf $DEMO_DIR" EXIT

log_info "Testing enhanced merge in $DEMO_DIR"

cd "$DEMO_DIR"
git init
git config user.name "Test User"
git config user.email "test@example.com"

echo "# Test Repo" > README.md
git add README.md
git commit -m "Initial commit"

# Test with files
BRANCH_NAME="rapper/test-feature"
WORKTREE_PATH="$DEMO_DIR/worktree"

git worktree add -b "$BRANCH_NAME" "$WORKTREE_PATH"
log_ok "Created worktree with branch: $BRANCH_NAME"

# Add files to worktree
cd "$WORKTREE_PATH"
echo "print('Hello!')" > feature.py
mkdir -p lib
echo "VERSION = '1.0'" > lib/config.py

log_info "Files created in worktree:"
find . -name "*.py" -type f

# Create mock task
TASKS_DIR="$HOME/.rapper/tasks"
mkdir -p "$TASKS_DIR"
TASK_ID="test-$(date +%s)"

cat > "$TASKS_DIR/$TASK_ID.json" <<EOF
{
  "id": "$TASK_ID",
  "name": "test-feature",
  "status": "completed",
  "branch_name": "$BRANCH_NAME",
  "worktree_path": "$WORKTREE_PATH",
  "workdir": "$DEMO_DIR",
  "repo_workdir": "$DEMO_DIR"
}
EOF

log_ok "Created mock task: $TASK_ID"

# Test merge
cd "$DEMO_DIR"
log_info "Testing: $RAPPER_DIR/rapper --merge $TASK_ID"

if "$RAPPER_DIR/rapper" --merge "$TASK_ID"; then
    log_ok "Merge completed successfully!"

    if [[ -f "feature.py" ]] && [[ -f "lib/config.py" ]]; then
        log_ok "✅ All files merged correctly"
    else
        log_error "❌ Files missing after merge"
    fi
else
    log_error "❌ Merge failed"
fi

# Cleanup
rm -f "$TASKS_DIR/$TASK_ID.json"

log_ok "Test completed"