package limiter

import (
	"errors"
	"testing"
	"time"
)

func TestNew(t *testing.T) {
	tests := []struct {
		name    string
		limit   int
		window  time.Duration
		wantErr error
	}{
		{name: "valid arguments", limit: 1, window: time.Second, wantErr: nil},
		{name: "zero limit", limit: 0, window: time.Second, wantErr: ErrInvalidLimit},
		{name: "negative limit", limit: -3, window: time.Second, wantErr: ErrInvalidLimit},
		{name: "zero window", limit: 1, window: 0, wantErr: ErrInvalidWindow},
		{name: "negative window", limit: 1, window: -time.Second, wantErr: ErrInvalidWindow},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			l, err := New(tc.limit, tc.window)
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("New(%d, %v) error = %v, want %v", tc.limit, tc.window, err, tc.wantErr)
			}
			if tc.wantErr == nil && l == nil {
				t.Fatalf("New(%d, %v) returned nil limiter without error", tc.limit, tc.window)
			}
		})
	}
}

func TestAllowWithinLimit(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	l, err := New(5, time.Minute)
	if err != nil {
		t.Fatalf("New returned unexpected error: %v", err)
	}

	tests := []struct {
		name   string
		offset time.Duration
		want   bool
	}{
		{name: "first call is allowed", offset: 0, want: true},
		{name: "second call is allowed", offset: 10 * time.Second, want: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := l.Allow("user-1", start.Add(tc.offset))
			if got != tc.want {
				t.Fatalf("Allow at offset %v = %v, want %v", tc.offset, got, tc.want)
			}
		})
	}
}

func TestAllowNewWindowResets(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	l, err := New(1, time.Minute)
	if err != nil {
		t.Fatalf("New returned unexpected error: %v", err)
	}

	tests := []struct {
		name   string
		offset time.Duration
		want   bool
	}{
		{name: "first window call", offset: 0, want: true},
		{name: "call well after the window ended", offset: 2 * time.Minute, want: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := l.Allow("user-1", start.Add(tc.offset))
			if got != tc.want {
				t.Fatalf("Allow at offset %v = %v, want %v", tc.offset, got, tc.want)
			}
		})
	}
}
