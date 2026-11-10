//! Upload quota checks and human-readable size parsing.

/// Maximum size of a single upload in bytes (10 MiB). The limit is inclusive:
/// an upload of exactly `MAX_UPLOAD_BYTES` bytes is accepted.
pub const MAX_UPLOAD_BYTES: u64 = 10 * 1024 * 1024;

/// Reasons why an upload is rejected by [`validate_upload`].
#[derive(Debug, PartialEq, Eq)]
pub enum UploadError {
    /// The upload has zero bytes.
    Empty,
    /// The upload is larger than `max` (which is always `MAX_UPLOAD_BYTES`).
    TooLarge { size: u64, max: u64 },
    /// Adding the upload to the already used bytes would exceed the quota.
    /// `needed` is the upload size; `available` is the quota minus the used
    /// bytes, saturating at zero.
    QuotaExceeded { needed: u64, available: u64 },
    /// `used_bytes + size_bytes` does not fit in a `u64`.
    Overflow,
}

/// Checks whether an upload of `size_bytes` may be stored, given that
/// `used_bytes` are already in use and the total budget is `quota_bytes`.
///
/// The checks are applied in this order:
/// 1. A size of zero is rejected with `UploadError::Empty`.
/// 2. A size greater than `MAX_UPLOAD_BYTES` is rejected with `TooLarge`.
///    A size equal to `MAX_UPLOAD_BYTES` is accepted.
/// 3. If `used_bytes + size_bytes` overflows `u64`, `UploadError::Overflow`
///    is returned. The sum is computed with `checked_add`.
/// 4. If the sum is greater than `quota_bytes`, `QuotaExceeded` is returned.
///    A sum equal to `quota_bytes` is accepted.
///
/// Returns `Ok(())` when all checks pass.
pub fn validate_upload(size_bytes: u64, used_bytes: u64, quota_bytes: u64) -> Result<(), UploadError> {
    if size_bytes == 0 {
        return Err(UploadError::Empty);
    }
    if size_bytes > MAX_UPLOAD_BYTES {
        return Err(UploadError::TooLarge {
            size: size_bytes,
            max: MAX_UPLOAD_BYTES,
        });
    }
    let total = match used_bytes.checked_add(size_bytes) {
        Some(total) => total,
        None => return Err(UploadError::Overflow),
    };
    if total > quota_bytes {
        return Err(UploadError::QuotaExceeded {
            needed: size_bytes,
            available: quota_bytes.saturating_sub(used_bytes),
        });
    }
    Ok(())
}

/// Parses a size string into a number of bytes.
///
/// The accepted form is an unsigned decimal integer, optionally followed by
/// optional whitespace and a unit. Leading and trailing whitespace is ignored.
/// Units are matched case-insensitively:
/// - no unit or `B` means 1 byte,
/// - `KB` means 1000 bytes,
/// - `MB` means 1000 * 1000 bytes,
/// - `KiB` means 1024 bytes,
/// - `MiB` means 1024 * 1024 bytes.
///
/// Examples of accepted input are "512", "10KB", "3MiB" and " 2 kib ".
///
/// Returns `None` for an empty or whitespace-only string, for a leading sign
/// such as "-5", for a missing number, for an unknown unit, and when the
/// result does not fit in a `u64`.
pub fn parse_size(input: &str) -> Option<u64> {
    let trimmed = input.trim();
    let digits_end = trimmed
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(trimmed.len());
    if digits_end == 0 {
        return None;
    }
    let number: u64 = trimmed[..digits_end].parse().ok()?;
    let unit = trimmed[digits_end..].trim().to_ascii_lowercase();
    let multiplier: u64 = match unit.as_str() {
        "" | "b" => 1,
        "kb" => 1000,
        "mb" => 1000 * 1000,
        "kib" => 1024,
        "mib" => 1024 * 1024,
        _ => return None,
    };
    number.checked_mul(multiplier)
}
