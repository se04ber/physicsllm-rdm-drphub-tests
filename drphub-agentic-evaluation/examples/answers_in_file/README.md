# Cases you already answered

No `system.json`, so nothing is invoked. The answers are in the file and the
harness scores them. This is the lowest bar there is, and a complete use of
the harness.

Three cases, one for each outcome:

| Case | Expected | Actual | Result |
| --- | --- | --- | --- |
| `xrr_acronym` | `XRR` | `XRR` | exact |
| `p08_facility` | `PETRA III` | `Petra III` | normalised, capitalisation only |
| `wavelength_unit` | `metre` | `seconds` | a real difference |

So the gate fails on one of three, correctly. Latency and tokens read
`not_available`, because nothing ran.
