# A benchmark you already ran

No `system.json`, so nothing is invoked: the answers are in the file and the
harness only scores them. This is the shape to use when you ran your own
system and want the answers graded, and it is the lowest bar there is.

The three cases are chosen to show all three outcomes at once:

| Case | Expected | Actual | Result |
| --- | --- | --- | --- |
| `xrr_acronym` | `XRR` | `XRR` | exact match |
| `p08_facility` | `PETRA III` | `Petra III` | passes on normalisation, capitalisation only |
| `wavelength_unit` | `metre` | `seconds` | a real difference |

So the gate is `fail`, correctly, on one case out of three. Latency and tokens
are `not_available`, because nothing ran.
