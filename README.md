# S4P access probe

From wherever REANA places the job, which routes to DESY S4P storage work?

Checks DNS, TCP, authenticated WebDAV `PROPFIND`, and whether the PNFS mount
is present — separately, because they fail for different reasons and the
difference is the finding.

Exits 0 either way: the report is the deliverable, so a red workflow would
mean the card broke rather than that the answer was "no". Reads
`S4P_BEARER_TOKEN` from the REANA secret store if one is configured; without
it the WebDAV leg still distinguishes *unreachable* from *reachable but
unauthorised*.
