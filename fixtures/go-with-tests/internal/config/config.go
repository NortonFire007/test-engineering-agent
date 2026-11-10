// Package config loads rate limiter settings from environment variables.
package config

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
)

const (
	defaultRateLimit         = 100
	minRateLimit             = 1
	maxRateLimit             = 10000
	defaultRateWindowSeconds = 60
	minRateWindowSeconds     = 1
	maxRateWindowSeconds     = 3600
)

// Config holds the rate limiter settings.
type Config struct {
	// RateLimit is the number of calls allowed per key per window (1..10000).
	RateLimit int
	// RateWindowSeconds is the window length in seconds (1..3600).
	RateWindowSeconds int
}

// Load reads RATE_LIMIT and RATE_WINDOW_SECONDS through getenv.
//
// A missing or blank variable takes its default: 100 for RATE_LIMIT and 60
// for RATE_WINDOW_SECONDS. Surrounding whitespace is ignored. A value that is
// not a decimal integer, or is outside its allowed range (RATE_LIMIT 1..10000,
// RATE_WINDOW_SECONDS 1..3600), returns an error and a zero Config. Both
// bounds are inclusive. A nil getenv returns an error.
func Load(getenv func(string) string) (Config, error) {
	if getenv == nil {
		return Config{}, errors.New("config: getenv must not be nil")
	}
	limit, err := intFromEnv(getenv, "RATE_LIMIT", defaultRateLimit, minRateLimit, maxRateLimit)
	if err != nil {
		return Config{}, err
	}
	window, err := intFromEnv(getenv, "RATE_WINDOW_SECONDS", defaultRateWindowSeconds, minRateWindowSeconds, maxRateWindowSeconds)
	if err != nil {
		return Config{}, err
	}
	return Config{RateLimit: limit, RateWindowSeconds: window}, nil
}

// intFromEnv reads key through getenv, applies def when the value is blank,
// and checks that the parsed value lies in [lo, hi].
func intFromEnv(getenv func(string) string, key string, def, lo, hi int) (int, error) {
	raw := strings.TrimSpace(getenv(key))
	if raw == "" {
		return def, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("config: %s: invalid integer %q: %w", key, raw, err)
	}
	if value < lo || value > hi {
		return 0, fmt.Errorf("config: %s: value %d is outside %d..%d", key, value, lo, hi)
	}
	return value, nil
}
