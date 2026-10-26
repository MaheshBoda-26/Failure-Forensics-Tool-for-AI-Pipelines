# Phase B-F Implementation Plan

## Overview
Implement all potential next steps from B to F:
- **B. Scale Demo** - Generate more documents, add failure types, larger eval suite
- **C. UI Improvements** - Add API key input to React UI, fix auth integration
- **D. Advanced Analytics** - More dashboard metrics, trend visualizations
- **E. Documentation Polish** - Demo video, interview talking points, ADR expansion
- **F. Custom Domain Work** - Domain-specific check plugins

---

## Phase B: Scale Demo (Week 1)

### B1: Document Generation Pipeline
- [ ] Create document generator with templates for each type
- [ ] Add more failure injection patterns
- [ ] Generate 100+ documents (20 per type × 5 types)
- [ ] Create 30+ seeded failures (6 per category × 5 categories)
- [ ] Update labels.json with new ground truth

### B2: Extended Eval Suite
- [ ] Create fault injection for all 7 fault types across all document types
- [ ] Build benchmark suite with 50+ test cases
- [ ] Add regression detection for each failure category
- [ ] Create eval performance baselines

### B3: Demo Data Validation
- [ ] Validate all generated documents against labels
- [ ] Run full demo and verify 100% seeded failure reproduction
- [ ] Create demo data export/import for CI

---

## Phase C: UI Improvements (Week 1-2)

### C1: React Auth Integration
- [ ] Add API key input field to Settings modal
- [ ] Implement Bearer token header injection in api/client.ts
- [ ] Add auth state management (login/logout)
- [ ] Show auth status in header (connected/disconnected)
- [ ] Handle 401 responses gracefully (redirect to login)

### C2: Enhanced Trace Explorer
- [ ] Add trace filtering by date range
- [ ] Add export CSV for analytics
- [ ] Improve DAG graph layout (React Flow)
- [ ] Add keyboard shortcuts
- [ ] Dark/light theme toggle

### C3: Real-time Features
- [ ] WebSocket connection for live trace updates
- [ ] Toast notifications for completed jobs
- [ ] Progress indicator for demo runs

---

## Phase D: Advanced Analytics (Week 2)

### D1: Enhanced Analytics API
- [ ] Add time-series endpoints (hourly/daily/weekly)
- [ ] Add per-model performance metrics
- [ ] Add cost breakdown by model/step
- [ ] Add failure correlation analysis

### D2: Dashboard Visualizations
- [ ] Interactive charts (Recharts/Chart.js)
- [ ] Failure heatmap by step × category × time
- [ ] Model performance comparison table
- [ ] Cost trends with projections
- [ ] Drill-down capability

### D3: Export & Reporting
- [ ] PDF report generation
- [ ] Scheduled email reports
- [ ] CSV/JSON export for all analytics

---

## Phase E: Documentation Polish (Week 2-3)

### E1: ADR Expansion
- [ ] ADR for job reaper design
- [ ] ADR for rate limiting architecture
- [ ] ADR for API key expiry
- [ ] ADR for PostgreSQL backend
- [ ] ADR for React auth integration

### E2: Demo Video
- [ ] Script 5-min demo walkthrough
- [ ] Record with brag/onetake
- [ ] Add captions and chapters
- [ ] Upload to GitHub/YouTube

### E3: Interview Materials
- [ ] 8 hardest interview questions with answers
- [ ] Architecture diagram (updated)
- [ ] Trade-off analysis document
- [ ] Performance benchmarks

### E4: User Guides
- [ ] Admin guide (auth, queue, scheduler)
- [ ] Developer guide (custom checks, plugins)
- [ ] Deployment guide (Render, PostgreSQL, Docker)

---

## Phase F: Custom Domain Work (Week 3)

### F1: Check Plugin Examples
- [ ] PII detection in summaries
- [ ] Financial amount validation
- [ ] Legal clause completeness
- [ ] Contract date consistency
- [ ] Invoice total verification

### F2: Domain-Specific Analyzers
- [ ] Contract analyzer (obligations, parties, terms)
- [ ] Invoice analyzer (line items, taxes, totals)
- [ ] Report analyzer (findings, recommendations)
- [ ] Correspondence analyzer (action items, deadlines)

### F3: Plugin Registry
- [ ] Create plugin marketplace structure
- [ ] Add plugin versioning
- [ ] Create plugin documentation template
- [ ] Build example plugins for common domains

---

## Testing & Quality Gates

### Automated Testing
- [ ] All 460+ existing tests pass
- [ ] New unit tests for each feature (>90% coverage)
- [ ] Integration tests for auth flow
- [ ] E2E tests for critical paths
- [ ] Load testing for rate limiting
- [ ] Chaos testing for job reaper

### Manual Verification
- [ ] Full demo run (15+ documents)
- [ ] Auth flow with API key
- [ ] Rate limiting enforcement
- [ ] Job reaper recovery
- [ ] PostgreSQL backend switch
- [ ] Analytics dashboard accuracy

---

## Dependencies & Order

```
B1 → B2 → B3 (can parallelize with C1)
C1 → C2 → C3 (depends on B1 for demo data)
D1 → D2 → D3 (depends on C1 for UI)
E1-E4 (can parallelize, needs B-D complete)
F1-F3 (depends on E1 for patterns)
```

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Tests passing | 100% (460+ → 600+) |
| Demo documents | 100+ (from 15) |
| Seeded failures | 30+ (from 5) |
| UI auth integration | Working end-to-end |
| Analytics charts | 10+ interactive visualizations |
| Documentation | 5+ new guides, demo video |
| Custom plugins | 10+ domain-specific checks |
| Test coverage | >90% for new code |
