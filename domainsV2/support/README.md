# Bundled domain support

This is a frozen source-and-data snapshot, not a dependency on a sibling repository.
Install it through the kit's `requirements.txt`. It supplies all five owning-domain
Python packages, their schemas and presentations, configurations, the shared inventory
movement helper, and the four reviewed fictional inventory datasets.

Only the inventory inputs used by V2 are included: Austin grid inventory,
upstream `normal_production`, and the explicit midstream and downstream review
directories. Unrelated generated scenarios and legacy sample telemetry are not needed.
The original Flask gateway is not included or started. V2 has its own gateway.

`bundle-manifest.json` records every bundled source/data file and its SHA-256.
`scripts\Verify-Support.ps1`, in the kit root, detects missing or changed bytes.
The source revision identifies the base repository revision, not a clean release:
the snapshot includes the local domain changes needed by the V2 presentations.
All file-level notices are retained; this snapshot does not grant a new license.

All inventories and simulated records are fictional and prohibited for operational
use. Reference-only manufacturer catalogs retain their citations and caveats.
Neither the generator coefficients nor stock thresholds claim industrial calibration.
Authored replay fixtures live separately in `energy_domains_v2\replay_data`.
Their manifest records the inputs at authoring time; portability edits do not
retroactively change that provenance or regenerate the reviewed replay.

For a deliberate source update, replace only the intended source/data files,
review the changes and update their manifest hashes. Preserve UTF-8/LF data and
the `.gitattributes` byte-preservation rules. Run the standalone checks; if
re-authoring replay, run the fixture builder offline and repeat the replay tests.
Do not silently refresh this snapshot from an unrelated parent repository.
