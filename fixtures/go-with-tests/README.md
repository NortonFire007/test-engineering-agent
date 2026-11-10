# ratelimit

A standard-library-only Go module with a fixed-window rate limiter and an
environment-based configuration loader.

`limiter.New(limit, window)` fails when limit or window is not greater than zero.
`Allow(key, now)` permits up to `limit` calls per key in each window. A window
starts at the first call for a key and lasts `window`. A call at exactly
start+window begins a new window. An empty key is always denied.
`Remaining(key, now)` reports the calls still allowed without changing state.

`config.Load(getenv)` reads `RATE_LIMIT` (default 100, range 1..10000) and
`RATE_WINDOW_SECONDS` (default 60, range 1..3600). Blank values take the default,
and out-of-range or non-integer values are errors. Both bounds are inclusive.

Run tests: `go test ./...`
