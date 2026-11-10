# Test fixtures

Synthetic projects used to validate language-independent stack detection in the
test-engineering agent. They are not real applications.

- `rust-no-tests/`: a Rust library (`quota`) with no tests, no git repository and
  no dependencies. It covers upload quota validation and size parsing, and should
  be detected as Rust with no test suite.
- `go-with-tests/`: a Go module (`example.com/ratelimit`) with an incomplete
  existing test suite in `limiter/`, plus an `internal/config` package without
  tests. Run its tests with `go test ./...`.

The `.env` files in these fixtures contain dummy values on purpose. They are
used to check that tools refuse to read secrets. Do not replace them with real
credentials.

Never run an agent workflow in place against these fixtures. Copy the fixture
to a temporary directory first, and run the workflow only on that copy.
