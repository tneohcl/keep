# Instructions: Prepare Keep for a Consumer Production Release

Continue developing Keep toward **version 1.0**, using the current personal beta as the baseline. Work through the priorities below in order. Fix issues, run appropriate checks, and document evidence before marking a phase complete.

## Working rules

- Preserve existing backups, credentials, settings, and recovery compatibility.
- Prioritize recovery reliability over new features.
- Maintain a consistent native Qt design across backup and restore.
- Assign distinct version numbers to distributed builds.
- Clearly distinguish automated verification from real-machine tests.
- Do not claim production readiness while required acceptance checks remain incomplete.
- Prepare release artifacts for review; do not publish publicly without explicit authorization.

## 1. Define and stabilize the supported baseline

- Specify supported Linux distributions, desktop environments, and dependency versions.
- Freeze unrelated feature development.
- Resolve known layout, selection, checkbox, menu, and keyboard issues.
- Verify light/dark themes, display scaling, small screens, and long application names.
- Show the application version in About and diagnostic logs.

**Acceptance:** No known blocking usability issues in supported environments.

## 2. Validate recovery and failure handling

- Restore onto a clean machine using saved credentials and recovery material.
- Compare restored contents, permissions, and symbolic links with originals.
- Test unavailable network destinations, disconnected drives, full disks, and interrupted operations.
- Ensure incomplete or failed operations never report success.
- Verify retention cannot prune another backup job's archives.
- Confirm existing backups remain usable after failures.

**Acceptance:** Repeatable recovery and failure tests pass, with documented results.

## 3. Make installation and scheduling reliable

- Detect conflicting legacy backup schedules and provide an explained migration.
- Use stable installed paths so moving a source folder cannot break scheduled backups.
- Validate installation, upgrades, uninstall, reboot, missed schedules, and logged-out behavior.
- Explain scheduling requirements and destination availability.
- Preserve user settings, credentials, and archives during upgrades.

**Acceptance:** A new user can configure automatic backups without terminal commands or manual file edits.

## 4. Provide backup-health monitoring

- Add integrity checks with progress, cancellation where safe, and clear results.
- Distinguish routine checks from deeper data verification.
- Surface missed backups, unavailable destinations, and maintenance failures.
- Provide actionable errors and privacy-conscious support logs.

**Acceptance:** Users can identify backup health and understand required corrective actions.

## 5. Complete release preparation

- Finish first-run setup and recovery-key guidance.
- Document installation, scheduling, restoration, upgrades, troubleshooting, and uninstall.
- Produce versioned packages, checksums, release notes, and rollback instructions.
- Review credential storage and diagnostic output for accidental disclosure.

**Acceptance:** An unfamiliar user can install Keep and recover files using the documentation.

## 6. Validate a release candidate

- Run a controlled trial for at least 2–4 weeks on supported systems.
- Include scheduled backups, upgrades, integrity checks, and real restores.
- Track defects by severity and repeat affected checks after fixes.
- Keep critical and high-severity defects as release blockers.

**Acceptance:** The trial completes with no unresolved critical or high-severity issues and all required acceptance checks documented.

## Required deliverables

- Prioritized release checklist with completion evidence.
- Implemented fixes and relevant regression coverage.
- Supported-environment and known-limitations documentation.
- Tested release-candidate package and checksums.
- Consumer installation and recovery instructions.
- Final readiness assessment identifying any remaining blockers.

## Execution order

Recovery and failure testing → installation and scheduling → backup health → final UI polish and documentation → release-candidate trial → 1.0 release review.
