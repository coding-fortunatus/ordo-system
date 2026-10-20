# Ordo

**Examination operations, ordered from planning to paperwork.**

Ordo is an examination-management platform for educational institutions. It brings academic data preparation, examination timetabling, venue distribution, candidate seating, operational monitoring, and examination documents into one coordinated workflow.

## What Ordo covers

- Academic structures, courses, classes, students, venues, and examination sessions
- Examination timetable generation and review
- Candidate distribution across halls
- Individual seat allocation and adjustment
- Progress monitoring for long-running planning work
- Operational exports, including schedules, seating plans, attendance materials, and related documents
- Role-based workflows for central administrators, department teams, faculty officers, and examination coordinators

## Who it is for

Ordo is designed for institutions that coordinate examinations across multiple departments, courses, venues, and administrative teams. It supports both institution-wide planning and delegated preparation by authorized staff.

## Product status

The documentation reflects the behavior and configuration found in the current repository. Capabilities are identified as verified, partially implemented, planned, inferred, referenced only, or unknown where appropriate. See the product intelligence summary and technical specification before making production, security, or commercial commitments.

## Repository guide

| Path | Purpose |
| --- | --- |
| `frontend/` | Dashboard web application (apex domain). The marketing site lives in a separate repo, `ordo-site`. |
| `ems/` | Examination-management domain and service implementation |
| `core/` | Application configuration and runtime setup |
| `docs/` | Product, marketing, and technical documentation |
| `testing/` | Exploratory testing resources |
| `.env.example` | Local development settings. Copy to `.env`. Production uses `deploy/.env.prod.example`. |

