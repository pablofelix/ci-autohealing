# CI Auto-Healing System

**Production-ready system for monitoring and diagnosing Konflux CI/CD pipeline failures**

---

## 🏗️ Repository Structure (Gitflow)

This repository uses **Gitflow** workflow:

- **`main`**: Production-ready releases (currently empty - not yet released)
- **`develop`**: Active development branch with current working code
- **`outdated`**: Archived old files and deprecated code for reference

---

## 🚀 Quick Start

```bash
# Switch to develop branch to see active code
git checkout develop

# See archived old code
git checkout outdated
```

---

## 📋 What's in Each Branch

### `develop` Branch (Active Development)

**Core System**:
- `collectors/python/` - Python data collectors (Kubernetes, KubeArchive, Unified)
- `cron/` - Automated collection scripts
- `db/` - Database schema and setup
- `ic` - CLI tool for querying and analyzing failures

**Documentation**:
- `AUTOMATIC_SYNC.md` - Automatic synchronization system
- `MULTI_API_SYSTEM.md` - Multi-API collection architecture
- `IMPROVED_DIAGNOSTICS.md` - Enhanced diagnostic features
- `IC_API_DESIGN.md` - API-style CLI design
- `DEVOPS_ANALYSIS.md` - DevOps analysis and recommendations

**Configuration**:
- `.env.example` - Environment variables template
- `docker-compose.yml` - Database container setup
- `requirements.txt` - Python dependencies
- `.gitignore` - Files to ignore

### `outdated` Branch (Archive)

Old versions and deprecated files:
- Previous collector implementations
- Old shell scripts
- Deprecated documentation
- Test files no longer used

---

## 🎯 System Overview

### What It Does

1. **Collects** failure data from Konflux CI/CD pipelines using multiple APIs
2. **Analyzes** failures with commit info, error messages, and TaskRun details  
3. **Synchronizes** component status (marks resolved when fixed)
4. **Provides CLI** for querying, analyzing, and diagnosing failures

### Architecture

```
┌─────────────────────────────────────────┐
│  Konflux CI/CD (Tekton Pipelines)       │
│  - Components build on push/PR          │
│  - PipelineRuns execute                 │
│  - Some fail, some succeed              │
└─────────────────────────────────────────┘
                ↓
┌─────────────────────────────────────────┐
│  Data Collection (Python)                │
│  ├─ Kubernetes API (current data)       │
│  ├─ KubeArchive API (archived data)     │
│  └─ OC Pods (logs fallback)             │
└─────────────────────────────────────────┘
                ↓
┌─────────────────────────────────────────┐
│  Database (PostgreSQL)                   │
│  - build_failures table                  │
│  - Stores metadata, logs, commits        │
└─────────────────────────────────────────┘
                ↓
┌─────────────────────────────────────────┐
│  CLI Tool (ic command)                   │
│  - Query failures                        │
│  - Analyze components                    │
│  - Get logs, commits, errors             │
└─────────────────────────────────────────┘
```

### Key Features

✅ **Multi-API Collection**: Uses 3 different APIs for maximum data coverage  
✅ **Automatic Sync**: Runs every 15 minutes via cron  
✅ **Status Tracking**: Distinguishes "currently failing" vs "historical failures"  
✅ **Comprehensive Metadata**: Commit SHA, URLs, error summaries, TaskRun details  
✅ **API-style CLI**: Field selectors, output formats, filters  
✅ **100% Metadata Coverage**: Even without logs, provides diagnostic info
│                  │    │  - Track in Langfuse         │
└──────────────────┘    └──────────────────────────────┘
```

## 📋 Prerequisites

- Python 3.9+
- PostgreSQL 12+ (same instance as Langfuse)
- OpenShift CLI (`oc`)
- Claude Code CLI (for `/ci-build` skill)
- Active OpenShift session

## 🚀 Quick Start

### 1. Clone and Setup

```bash
cd ~/claude/ci-autohealing
chmod +x setup.sh
./setup.sh
```

The setup script will:
- Check prerequisites
- Create Python virtual environment
- Install dependencies
- Create PostgreSQL database and schema
- Install Claude Code skill
- Configure environment

### 2. Configure Environment

Edit `.env` file:

```bash
# Database (same as Langfuse)
DB_HOST=localhost
DB_PASSWORD=your_password

# Anthropic
ANTHROPIC_API_KEY=sk-ant-your-key

# Langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-your-key
LANGFUSE_SECRET_KEY=sk-lf-your-key
```

### 3. Test Scanner

```bash
source venv/bin/activate
python3 collectors/scanner.py --mode trigger
```

You should see:
```
Scanning 8 components...
━━━━━━━━━━━━━━━━━━ 100% • 8/8 • 0:00:15

Scan Results
Scan ID: f47ac10b-58cc-4372-a567-0e02b2c3d479
Duration: 15.3s

┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Metric              ┃ Count ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ Components Scanned  │ 8     │
│ Total Failures      │ 3     │
│ New Failures        │ 1     │
└─────────────────────┴───────┘
```

## 💻 Usage

### Quick Data Collection (Shell Scripts)

#### Collect Failed PipelineRuns
```bash
./collectors/collect-simple.sh
```

Fast collector that gathers failed PipelineRuns from Konflux using `kubectl tekton` and `oc` commands.

#### Fetch Logs
```bash
# From active Kubernetes pods (<8 hours old)
./collectors/fetch-logs-from-pods.sh

# From KubeArchive API (older failures)
./collectors/fetch-logs-kubearchive.sh
```

### CLI Tool: `ic` (Infrastructure/CI)

Query build failures and component health:

```bash
# Build status
./ic build status                    # Overall health summary
./ic build failures --limit 20       # List failures
./ic build get <pipelinerun>         # Get details
./ic build logs <pipelinerun>        # View logs
./ic build recent --limit 10         # Recent failures

# Component health
./ic component list                  # List all components
./ic component get <name>            # Component details
./ic component failures <name>       # Component failures

# Statistics
./ic stats                          # Database stats
./ic stats daily                    # Failures by day

# Database
./ic db status                      # Check connection
./ic db query "<sql>"               # Custom query
```

See [README-CLI.md](README-CLI.md) for complete CLI documentation.

### Automatic Log Collection

Install cron job to collect logs every 2 hours:

```bash
./cron/install-cron.sh
```

This ensures logs are captured before pods are deleted.

### Scanner Modes (Python)

#### Trigger Mode (One-time Scan)
```bash
python3 collectors/scanner.py --mode trigger
```

Runs a single scan and exits. Good for:
- Manual checks
- Cron jobs
- CI/CD integration

#### Daemon Mode (Continuous)
```bash
python3 collectors/scanner.py --mode daemon --interval 300
```

Runs continuously, scanning every N seconds. Good for:
- Always-on monitoring
- Real-time failure detection

#### Component Mode (Specific Components)
```bash
python3 collectors/scanner.py --mode component \
  --components odh-trustyai-nemo-guardrails-server-v3-4
```

Scan only specific components.

### Claude Code Skill: `/ci-build`

From Claude Code, you can use:

#### Show Dashboard
```
/ci-build
```
Shows overview of all failures, component health, and AI metrics.

#### Trigger Scan
```
/ci-build scan
```
Runs scanner and reports new failures.

#### Analyze Component
```
/ci-build odh-trustyai-nemo-guardrails-server-v3-4
```
Deep analysis of specific component's latest failure.

#### Auto-Fix
```
/ci-build fix odh-trustyai-nemo-guardrails-server-v3-4
```
Analyze failure and create PR with fix (if confidence > 0.8).

#### Check Status
```
/ci-build status
```
Show all pending fixes and their status.

### Direct Database Queries

```bash
# Connect to database
psql -h localhost -U postgres -d konflux_monitoring

# Show active failures
SELECT * FROM active_failures;

# Show component health
SELECT * FROM component_health ORDER BY health_score ASC;

# Show AI performance
SELECT * FROM ai_performance;
```

### Python API (for custom scripts)

```python
from db.database import Database

db = Database()

# Get unresolved failures
failures = db.get_unresolved_failures(limit=10)

# Get failures needing analysis
to_analyze = db.get_failures_needing_analysis(limit=5)

# Get component health
health = db.get_component_health('odh-trustyai-v3-4')
```

## 📊 Database Schema

### Core Tables

**build_failures**: All build failures
- component_name, pipelinerun_name
- commit info (SHA, message, URL)
- error details, logs
- resolution tracking

**ai_analysis**: AI diagnosis of failures
- root_cause, failure_category
- confidence_score
- recommended_fix
- Langfuse trace IDs

**resolution_attempts**: Fix attempts (AI or manual)
- attempt_number, strategy
- PR info (URL, branch, commits)
- success tracking

**component_health**: Aggregated health metrics
- success_rate, consecutive_failures
- AI fix statistics
- health_score (0-100)

### Views

**active_failures**: Currently unresolved failures
**component_metrics**: Health metrics per component
**ai_performance**: AI success rates and costs

## 🤖 AI Remediation

### How It Works

1. **Detection**: Scanner finds new failure
2. **Triage**: Classify failure type (dependency, test, build, etc)
3. **Analysis**: Claude analyzes logs + code to find root cause
4. **Solution**: Generate fix with confidence score
5. **Decision**: Auto-fix if confidence > threshold
6. **PR Creation**: Create PR with fix + explanation
7. **Verification**: Monitor next build for success
8. **Learning**: Track outcome for future improvements

### Auto-Fix Categories

Currently auto-fixable with high confidence:
- **dependency_issue**: Version conflicts, missing packages
- **syntax_error**: Code syntax problems
- **config_error**: YAML/JSON configuration issues

Requires human review:
- **test_failure**: Failed tests (may indicate real bugs)
- **resource_limit**: OOM, timeout (needs capacity planning)
- **infrastructure**: Platform issues

### Confidence Scoring

AI provides confidence (0.0-1.0) based on:
- Error message clarity (0.3)
- Code change simplicity (0.3)
- Pattern recognition (0.2)
- Test coverage (0.2)

Auto-fix only happens if confidence ≥ 0.8 (configurable).

## 📈 Dashboards (Grafana)

### Setup Grafana Connection

1. Add PostgreSQL datasource:
```yaml
apiVersion: 1
datasources:
  - name: Konflux Monitoring
    type: postgres
    url: localhost:5432
    database: konflux_monitoring
    user: postgres
    secureJsonData:
      password: your_password
```

2. Import dashboards from `dashboards/`:
   - `failure_overview.json` - Build failures overview
   - `component_health.json` - Component health matrix
   - `ai_performance.json` - AI metrics and ROI

### Key Metrics

- **MTTR** (Mean Time To Repair): How fast failures are fixed
- **Fix Success Rate**: % of AI fixes that work
- **Component Health Score**: 0-100 per component
- **Failure Trends**: Daily/weekly failure counts
- **Cost Per Fix**: AI token usage and costs

## 🔧 Configuration

### Scanner Configuration

In `.env`:
```bash
# How often to scan (daemon mode)
SCANNER_INTERVAL=300  # 5 minutes

# How far back to look
SCANNER_LOOKBACK_HOURS=48

# Components file (or empty for all)
COMPONENTS_FILE=/path/to/components.txt

# Max PRs per scan (rate limiting)
SCANNER_MAX_PRS_PER_RUN=50
```

### AI Configuration

```bash
# Enable auto-fix
AI_AUTO_FIX_ENABLED=false  # Set to true when ready

# Minimum confidence for auto-fix
AI_MIN_CONFIDENCE=0.8

# Max attempts per failure
AI_MAX_FIX_ATTEMPTS=3

# Auto-fixable categories
AI_AUTO_FIX_CATEGORIES=dependency_issue,syntax_error,config_error
```

## 🎓 Examples

### Example 1: Analyze Recent Failure

```bash
# From Claude Code
/ci-build odh-trustyai-nemo-guardrails-server-v3-4
```

Output:
```
╔═══════════════════════════════════════════════════════════╗
║  FAILURE ANALYSIS                                         ║
╠═══════════════════════════════════════════════════════════╣
Component: odh-trustyai-nemo-guardrails-server-v3-4
Commit: 5df4295 - "Merge upstream/main"
Failed Task: build-container
Failed: 4 hours ago

ROOT CAUSE:
  Category: dependency_issue
  Confidence: 0.92

  Package 'nvidia-ml-py==12.535.77' not found.
  Available version is 12.535.161.
  Likely typo from upstream merge.

PROPOSED FIX:
  File: requirements.txt:42
  Change: nvidia-ml-py==12.535.77 → 12.535.161

Would you like me to create a PR with this fix?
```

### Example 2: Dashboard View

```bash
/ci-build
```

Output:
```
╔═══════════════════════════════════════════════════════╗
║         CI/CD BUILD HEALTH DASHBOARD                  ║
╠═══════════════════════════════════════════════════════╣
║ Active Failures: 7                                    ║
║ New (24h): 3                                          ║
║ Critical Components: 2                                ║
║ AI Fix Success Rate: 73%                              ║
╚═══════════════════════════════════════════════════════╝

Critical Components:
  • odh-trustyai-nemo-guardrails-server-v3-4 (3 failures)
  • odh-feature-server-v3-4 (2 failures)
```

### Example 3: Daemon Mode

```bash
python3 collectors/scanner.py --mode daemon --interval 300
```

Output:
```
Daemon mode started - scanning every 300s
Press Ctrl+C to stop

═══ Scan #1 at 2026-04-16 12:00:00 ═══
Scanning 8 components... ✓

Scan Results
Components Scanned: 8
New Failures: 1

Next scan in 300s...
```

## 🐛 Troubleshooting

### Scanner can't find components
```bash
# Check you're logged into OpenShift
oc whoami

# Check namespace
oc project NAMESPACE_PLACEHOLDER

# List components manually
oc get components -n NAMESPACE_PLACEHOLDER
```

### Database connection fails
```bash
# Test connection
psql -h localhost -U postgres -d konflux_monitoring -c '\dt'

# Check credentials in .env
cat .env | grep DB_
```

### Skill not working in Claude Code
```bash
# Check skill is installed
ls ~/.claude/skills/ci-build.skill.md

# Reinstall skill
cp skills/ci-build.skill.md ~/.claude/skills/
```

## 📚 Project Structure

```
ci-autohealing/
├── README.md                        # This file
├── GETTING-STARTED.md              # Setup guide
├── README-CLI.md                   # CLI documentation
├── ic                              # CLI tool executable
├── db-start.sh                     # Database management
├── db-stop.sh
├── db/
│   ├── schema.sql                  # PostgreSQL schema
│   ├── migrate.py                  # Migration script
│   └── database.py                 # Database operations
├── collectors/
│   ├── collect-simple.sh           # Fast shell-based collector
│   ├── fetch-logs-from-pods.sh     # Log collection from pods
│   ├── fetch-logs-kubearchive.sh   # Log collection from KubeArchive
│   └── scanner.py                  # Python scanner (daemon/trigger)
├── cron/
│   ├── install-cron.sh            # Install cron job
│   ├── uninstall-cron.sh          # Uninstall cron job
│   └── collect-logs.sh            # Cron job script
├── show-dashboard.sh              # Quick summary view
├── show-component-web.sh          # Component details with links
├── ai-remediation/
│   ├── analyzer.py                # AI failure analysis
│   └── fixer.py                   # AI fix generation
├── api/
│   └── main.py                    # FastAPI REST API
├── skills/
│   └── ci-build.skill.md          # Claude Code skill
├── agents/
│   └── ci-troubleshooter.agent.md # Agent definition
├── dashboards/
│   └── grafana/                   # Grafana dashboard JSONs
└── logs/
    └── cron/                      # Cron execution logs
```

## 🚧 Roadmap

- [ ] **Phase 1**: Database + Scanner (✓ Current)
- [ ] **Phase 2**: AI Analysis Engine
- [ ] **Phase 3**: Auto-Fix PR Creation
- [ ] **Phase 4**: Grafana Dashboards
- [ ] **Phase 5**: Webhook Integration
- [ ] **Phase 6**: Slack Notifications

## 📝 License

Internal tool for Red Hat RHOAI team.

## 🤝 Contributing

Contact: @operator

## 🔗 Related

- [Langfuse](http://localhost:3000) - Observability dashboard
- [Konflux UI](https://console.redhat.com/preview/application-pipeline) - Build pipeline UI
- [Claude Code Docs](https://docs.anthropic.com/claude/docs) - Claude Code documentation
