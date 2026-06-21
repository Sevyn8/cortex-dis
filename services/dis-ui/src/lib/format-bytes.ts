// Human-readable byte sizes for the upload file card (e.g. "12 B", "3.4 KB", "1.2 MB").
// Binary units (1 KB = 1024 B), one decimal for KB and up, none for bytes. Negative or
// non-finite inputs clamp to "0 B" (a file size is never negative; we never fabricate one).
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B'
  }
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  // Bytes are whole; KB and up show one decimal (trailing ".0" trimmed).
  const text = unit === 0 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, '')
  return `${text} ${units[unit]}`
}
