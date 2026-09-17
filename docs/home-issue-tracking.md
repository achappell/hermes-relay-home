# Hermes Home issue tracking

Home keeps story identity and delivery status in
_bmad-output/implementation-artifacts/story-index.yaml and
sprint-status.yaml. The active GitHub Issues board is the external work record;
the local sprint status remains the delivery authority.

## Home workflow overrides

Home-specific BMAD workflow sources live in
_bmad/custom/home-issue-tracking/workflows. BMAD setup writes generated copies
under _bmad/_config/custom/workflows, so setup can replace those copies without
changing the shared bmad-issue-tracking-setup skill.

After running or refreshing issue-tracking setup, restore the Home behavior:

    scripts/apply_home_issue_tracking_overrides.sh

Check whether generated copies match the Home sources:

    scripts/apply_home_issue_tracking_overrides.sh --check

When the shared BMAD workflow implementation changes, review its new generated
copy and carry forward any needed upstream fixes before reapplying the Home
source. These are complete Home-specific workflow copies, so applying them
intentionally replaces the matching generated files.

## Story workflow behavior

- Story issues resolve from the exact github_issue URL in story-index.yaml. If
  that is missing, the workflow searches for the exact sprint key and refuses
  an ambiguous match instead of selecting a similarly named issue.
- New issue URLs are written to the matching story-index record and committed
  in a file-only commit after the diff is displayed. The helper refuses to
  write when that file already has local changes. If an existing issue cannot
  be identified by URL, title, or sprint key, add its exact URL to the local
  story record before running completion again.
- The PRD key comes from a feat/{prd_key}/{story_key} branch when the PRD file
  is outside this repository.
- Story PRs target issue_tracking.story_base_branch in
  _bmad/custom/issue-tracking.yaml; Home sets this to main.
- GitHub CI status comes from checks on the story PR. A missing PR or checks
  that never start is an incomplete gate, not a passing result.
- Status labels are ensured before the workflow updates them. Only a passing
  PR check allows a completed story to be marked done.
