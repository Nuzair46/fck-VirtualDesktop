Managed APK compatibility profile

apk-managed-v1.json identifies named method signatures and hashes normalized IL
semantics for 15 required methods across Android, Mobile, Net, and Interfaces.
It also checks selected call/constant patterns and the Quest platform value.
The profile contains signatures and digests, not a copy of the application's IL.

Normal patching does not require a particular assembly hash or version number.
The normalizer ignores metadata tokens, MVIDs, assembly versions, branch byte
offsets, and equivalent short/long IL encodings. It retains instruction order,
control-flow targets, locals, exception regions, constants, and referenced member
identities. Changes elsewhere in an assembly can therefore be accepted while a
changed required method is rejected before either output DLL is written.

Compatibility probe:
  tools\PatchManaged.exe --probe ORIGINAL_DLL_DIRECTORY

Patch API remains:
  tools\PatchManaged.exe ORIGINAL_DLL_DIRECTORY OUTPUT_DLL_DIRECTORY ACCOUNT SIGNATURE_BASE64 CERT_DER

Either command accepts --profile PROFILE_JSON. The default profile path is
relative to the executable: ..\profiles\apk-managed-v1.json.

The probe returns JSON with compatible, profile_id, source_sha256, and methods.
The patch report records actual input hashes for provenance, matched profile
and method digests, preserved tokens/MVIDs, and runtime_verified=false.

A changed signature or behavior requires a new reviewed profile and potentially
a recipe change. The explicit --generate-baseline-profile maintainer command
accepts only the already-reviewed 1.34.25 baseline; it is not an automatic way
to approve arbitrary future inputs. Profile matching is static compatibility
evidence and does not establish headset operation or performance.

Integration checks are in src/test_managed_profiles.py. They create copies with
changed version/MVID, shifted metadata tokens, and alternate IL macro encodings;
these must still probe and patch successfully. Changed certificate constants
and inverted branch conditions must fail before any output folder is created.
Results are recorded in managed-profile-test-results.json.
