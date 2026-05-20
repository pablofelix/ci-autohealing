# Git Branch Guide

**Repository**: ci-autohealing  
**Workflow**: Gitflow  
**Date**: 2026-04-17

---

## 🌳 Branch Structure

```
main (production releases - currently empty)
│
├── develop (active development)
│   ├── feature/* (new features)
│   └── fix/* (bug fixes)
│
└── outdated (archived old code)
```

---

## 📋 Branch Descriptions

### `main` Branch

**Purpose**: Production-ready releases  
**Current Status**: Empty (no releases yet)  
**Protected**: Yes (requires review)

**What's in main**:
- `.gitignore` - Files to ignore
- `README.md` - Repository overview and structure

**When to merge to main**:
- When develop is stable and tested
- Ready for production deployment
- Tag with version number (v1.0.0, v1.1.0, etc.)

**Commands**:
```bash
# View main branch
git checkout main

# Merge develop to main (when ready for release)
git checkout main
git merge develop
git tag v1.0.0
git push origin main --tags
```

---

### `develop` Branch

**Purpose**: Active development and integration  
**Current Status**: ✅ Active (all current work here)  
**Default branch**: Yes

**What's in develop**:

**Core System**:
- `src/` - Python data collectors
  - `collect_comprehensive.py` - Main collector
  - `sync_component_status.py` - Status synchronization
  - `unified_collector.py` - Multi-API orchestrator
  - `kubernetes_client.py` - Kubernetes API client
  - `kubearchive_client.py` - KubeArchive API client
  - `tekton_results_client.py` - Tekton Results client
  - `pipelinerun_details.py` - Detailed failure analysis
  - `models.py`, `database.py`, `config.py` - Core libraries

- `cron/` - Automated collection
  - `collect-comprehensive.sh` - Main cron job (runs every 15 min)
  - `install-cron.sh`, `uninstall-cron.sh` - Cron management

- `db/` - Database
  - `schema.sql` - Database schema
  - `migrate.py`, `migrate.sh` - Migration scripts

- `ic-current` - CLI tool (will become `ic` when enhanced version ready)

**Documentation** (up-to-date):
- `AUTOMATIC_SYNC.md` - Automatic sync system explained
- `MULTI_API_SYSTEM.md` - Multi-API architecture
- `IMPROVED_DIAGNOSTICS.md` - Enhanced diagnostics
- `IC_API_DESIGN.md` - CLI API design
- `DEVOPS_ANALYSIS.md` - Complete DevOps analysis

**Configuration**:
- `.env.example` - Environment template
- `docker-compose.yml` - Database container
- `requirements.txt` - Python dependencies
- `db-start.sh`, `db-stop.sh` - Database management
- `setup.sh` - Initial setup script

**When to use develop**:
- All new feature development
- Bug fixes
- Documentation updates
- Active work

**Commands**:
```bash
# Work on develop
git checkout develop

# Create feature branch
git checkout -b feature/my-feature develop

# After finishing feature
git checkout develop
git merge feature/my-feature
git branch -d feature/my-feature

# Sync with remote
git pull origin develop
git push origin develop
```

---

### `outdated` Branch

**Purpose**: Archive for deprecated/old files  
**Current Status**: Archive only (read-only reference)  
**Protected**: No (but don't commit here unless archiving)

**What's in outdated**:

**Old CLI versions**:
- `ic-old` - Previous ic implementation
- `ic.backup` - Backup before changes
- `ic-new` - API-style prototype (features merged to ic-current)

**Deprecated documentation**:
- `ADMIN-GUIDE.md` - Old admin guide
- `COMPREHENSIVE_SYSTEM.md` - Superseded by current docs
- `GETTING_STARTED.md` / `GETTING-STARTED.md` - Duplicates
- `IC_COMPARISON.md`, `IC_EXAMPLES.md` - Old CLI docs
- `KUBEARCHIVE-ACCESS.md` - Superseded by MULTI_API_SYSTEM.md
- `LOGS-FETCHING.md` - Old log fetching docs
- `QUICK-START.md` - Superseded by AUTOMATIC_SYNC.md
- `README-CLI.md` - Old CLI readme
- `SETUP_COMPLETE.md` - Old setup guide
- `SUMMARY.md` - Old summary (superseded by DEVOPS_ANALYSIS.md)
- `SYNC_SYSTEM.md` - Old sync docs
- `TRIAGE_GUIDE.md` - Old triage guide

**Deprecated shell scripts**:
- `ka-functions.sh` - Old KubeArchive shell functions
- `kubectl-ka-wrapper.sh` - Old wrapper (replaced by Python client)
- `show-*.sh` - Old display scripts (replaced by ic command)
- `test-*.sh` - Old test scripts

**When to use outdated**:
- Reference old implementations
- Find historical decisions/approaches
- Restore something if needed

**Commands**:
```bash
# View outdated branch
git checkout outdated

# Find old file
git checkout outdated -- path/to/old/file.sh

# Archive new deprecated file
git checkout outdated
git add deprecated-file.sh
git commit -m "archive: Add deprecated-file.sh"
git checkout develop
```

---

## 🔄 Workflow Examples

### Creating a New Feature

```bash
# 1. Start from develop
git checkout develop
git pull origin develop

# 2. Create feature branch
git checkout -b feature/add-log-archival

# 3. Work on feature
# ... make changes ...
git add .
git commit -m "feat: Add S3 log archival"

# 4. Merge back to develop
git checkout develop
git merge feature/add-log-archival

# 5. Clean up
git branch -d feature/add-log-archival
git push origin develop
```

### Releasing to Production

```bash
# 1. Ensure develop is ready
git checkout develop
# Run tests, verify everything works

# 2. Merge to main
git checkout main
git merge develop

# 3. Tag release
git tag -a v1.0.0 -m "Release v1.0.0: Production-ready CI auto-healing"

# 4. Push
git push origin main --tags

# 5. Back to develop
git checkout develop
```

### Archiving Old Files

```bash
# 1. Switch to outdated
git checkout outdated

# 2. Add deprecated files
git add old-script.sh
git commit -m "archive: Add old-script.sh (superseded by new-implementation.py)"

# 3. Back to develop
git checkout develop

# 4. Remove from develop if it's there
git rm old-script.sh
git commit -m "refactor: Remove old-script.sh (moved to outdated branch)"
```

---

## 📊 Current Status

**Last Updated**: 2026-04-17

**Branch Status**:
- ✅ `main`: Initialized (empty, ready for first release)
- ✅ `develop`: Active (58 files, production-ready code)
- ✅ `outdated`: Archive (28 files, historical reference)

**Commits**:
- `main`: 1 commit (initial structure)
- `develop`: 2 commits (initial + active files)
- `outdated`: 2 commits (initial + archived files)

**Next Steps**:
1. Merge enhanced `ic` command when agent finishes
2. Test all functionality in develop
3. When stable, merge develop → main for v1.0.0

---

## 🔒 Protected Files

**Never commit these** (in .gitignore):
- `.env` - Environment secrets
- `logs/` - Log files
- `data/` - Database data
- `venv/` - Python virtual environment
- `__pycache__/` - Python cache
- `.claude/` - Claude Code temporary files

**Always keep these**:
- `.gitignore` - Ignore rules
- `.env.example` - Environment template
- `README.md` - Repository overview
- Documentation (*.md files in develop)
- Source code (collectors/, db/, cron/)
- Configuration (docker-compose.yml, requirements.txt)

---

## 📖 Documentation Standards

### In `develop` Branch

**Keep documentation**:
- ✅ Up-to-date
- ✅ Accurate
- ✅ Tested
- ✅ Referenced by current code

**Documentation types**:
- System architecture (MULTI_API_SYSTEM.md)
- Features (AUTOMATIC_SYNC.md, IMPROVED_DIAGNOSTICS.md)
- Design decisions (IC_API_DESIGN.md, DEVOPS_ANALYSIS.md)
- User guides (README.md)

### In `outdated` Branch

**Documentation that**:
- ❌ Superseded by newer docs
- ❌ Describes deprecated features
- ❌ Contradicts current implementation
- ✅ Useful for historical reference

---

## 🚀 Quick Reference

```bash
# Daily work
git checkout develop           # Start working
git pull origin develop        # Get latest changes
# ... make changes ...
git add .                      # Stage changes
git commit -m "type: message"  # Commit
git push origin develop        # Share changes

# Check what branch you're on
git branch

# See branch history
git log --oneline --graph --all

# Compare branches
git diff main develop          # See what's new in develop
git diff develop outdated      # See what's archived

# List all branches
git branch -a

# Delete local branch
git branch -d feature/old-feature

# View files in another branch without switching
git show outdated:old-file.sh
```

---

## 📞 Help

**Branch confusion?**
- Run `git branch` to see which branch you're on (marked with *)
- Run `git status` to see what's changed
- Run `git log --oneline -5` to see recent commits

**Accidental commit to wrong branch?**
```bash
# If you committed to main but meant develop:
git checkout develop
git cherry-pick <commit-hash>  # Copy commit to develop
git checkout main
git reset --hard HEAD~1        # Remove from main
```

**Need old file from outdated?**
```bash
# View file
git show outdated:path/to/file.sh

# Restore to working directory (doesn't commit)
git checkout outdated -- path/to/file.sh
```

---

**Maintained by**: DevOps Team  
**Workflow**: Gitflow  
**Protected Branches**: main
