# quota

A dependency-free Rust library that checks upload sizes against a quota and
parses human-readable size strings.

`validate_upload(size_bytes, used_bytes, quota_bytes)` returns `Ok(())` or an
error, checked in this order: `Empty` for zero bytes, `TooLarge` when the size
exceeds `MAX_UPLOAD_BYTES` (10 MiB, inclusive), `Overflow` when the sum overflows
`u64`, and `QuotaExceeded` when the new total exceeds the quota (equal is allowed).

`parse_size` accepts an unsigned integer with an optional unit: none or `B`,
`KB` (1000), `MB` (1000^2), `KiB` (1024), `MiB` (1024^2). Units are
case-insensitive. It returns `None` for empty input, a sign, an unknown unit,
a missing number, or overflow.

The exact contract is in the doc comments in `src/lib.rs`.
