// Package limiter implements a fixed-window rate limiter keyed by string.
// Time is always supplied by the caller, so behaviour is deterministic.
package limiter

import (
	"errors"
	"time"
)

var (
	// ErrInvalidLimit is returned by New when limit is not greater than zero.
	ErrInvalidLimit = errors.New("limiter: limit must be greater than zero")
	// ErrInvalidWindow is returned by New when window is not greater than zero.
	ErrInvalidWindow = errors.New("limiter: window must be greater than zero")
)

// bucket holds the state for one key: the start of its current window and
// the number of allowed calls inside that window.
type bucket struct {
	start time.Time
	count int
}

// Limiter allows at most limit calls per key in each window of length window.
// A Limiter must be created with New.
type Limiter struct {
	limit   int
	window  time.Duration
	buckets map[string]*bucket
}

// New returns a Limiter that allows limit calls per key per window.
// It returns ErrInvalidLimit if limit <= 0 and ErrInvalidWindow if window <= 0.
func New(limit int, window time.Duration) (*Limiter, error) {
	if limit <= 0 {
		return nil, ErrInvalidLimit
	}
	if window <= 0 {
		return nil, ErrInvalidWindow
	}
	return &Limiter{
		limit:   limit,
		window:  window,
		buckets: make(map[string]*bucket),
	}, nil
}

// Allow reports whether a call for key at time now is permitted, and records
// it if so.
//
// The first call for a key starts that key's window at now. A window covers
// the half-open interval [start, start+window): a call at exactly
// start+window is in a new window, so the boundary instant is inclusive for
// the new window. When a new window starts, the count is reset to zero.
// Within a window, up to limit calls are allowed; the limit-th call is
// allowed and every call after it in the same window is denied. Denied calls
// do not change the count.
//
// An empty key is always denied and creates no state.
func (l *Limiter) Allow(key string, now time.Time) bool {
	if key == "" {
		return false
	}
	b, ok := l.buckets[key]
	if !ok {
		b = &bucket{start: now}
		l.buckets[key] = b
	} else if !now.Before(b.start.Add(l.window)) {
		b.start = now
		b.count = 0
	}
	if b.count >= l.limit {
		return false
	}
	b.count++
	return true
}

// Remaining returns how many more calls for key would be allowed at time now
// without changing any state. It returns 0 for an empty key. For a key with
// no recorded calls, or whose window has ended at now, it returns the full
// limit. Otherwise it returns limit minus the calls already allowed in the
// current window, never less than zero.
func (l *Limiter) Remaining(key string, now time.Time) int {
	if key == "" {
		return 0
	}
	b, ok := l.buckets[key]
	if !ok || !now.Before(b.start.Add(l.window)) {
		return l.limit
	}
	remaining := l.limit - b.count
	if remaining < 0 {
		return 0
	}
	return remaining
}
