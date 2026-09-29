# V2 zone mode confirmation

This proposal changes only the native V2 path, not the legacy polling pathway
or V1 writes. It does not promise faster mechanical damper movement.

## Contract

- Mode selection returns after the existing command transport acknowledges it.
- A controller-owned worker first attempts readback after 250 ms, subject to
  the existing serialized transport and a shared two-second read-start limit.
- Multiple selections of one zone replace the earlier pending expectation.
  Commands remain ordered and are not dropped or resent by confirmation.
- A fresh contradictory mode is masked for at most six seconds after ACK.
  Afterwards the observed mode is published, while tracking continues for at
  most 30 seconds. Other fields, including temperatures, remain observed data.
- Normal polling can satisfy a pending confirmation. It no longer holds the
  command lock across the entire system/zone sweep; requests are still serial.
- A confirmation deadline includes time waiting for the lock and doing I/O.
  Expiry logs an unconfirmed selection but does not by itself mark the bridge
  offline. A real ConnectionError retains the existing disconnected/scan path.
- Controller close or discovery shutdown cancels owned confirmation work.

Explicit setpoint, fan, system-power and airflow commands retain their existing
confirmation behaviour. The existing AUTO-mode-only contribution is needed for
AUTO to take this fast path; otherwise the parent branch encodes AUTO as a
setpoint write. These changes should be reviewed together before release.

## Type=7 bulk-read assessment

A separate local adapter on one V2-only bridge has successfully read
`iZoneStatusV2.Zones` using Type=7. That is useful evidence, not a guarantee of
support across all firmware. The native branch currently has Type=1/2 fixtures,
and Type=7 may omit metadata available in a full Type=2 zone document.

This PR therefore does not silently switch polling to Type=7. A later bulk-read
implementation should have all of the following acceptance cases:

1. Explicit capability detection, with bounded Type=2 fallback for unsupported
   or incomplete responses; no parallel request storm.
2. Expected bridge identity, unique zone indices and complete zone coverage.
3. No merging of a partial failed bulk response with an unrelated fallback read.
4. Defined refresh/invalidation of names, types and any other cached metadata,
   including changed zone counts, endpoint replacement and controller reload.
5. Tests for rapid commands, stale in-flight snapshots and bounded confirmation
   that preserve the newest acknowledged selection.
6. Hardware validation on both a V2-only and a dual-stack bridge, plus unchanged
   V1 regressions. A complete native-branch hardware test remains outstanding.

The lower-risk immediate improvement is command interleaving between Type=2
reads plus bounded read-only confirmation. It needs no new firmware assumption.
