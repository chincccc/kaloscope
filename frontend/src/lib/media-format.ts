export function formatMediaDuration(value: number | null | undefined): string {
  if (!value || value <= 0) {
    return '';
  }
  const total = Math.round(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

export function formatMediaSize(value: number | null | undefined): string {
  if (value === null || value === undefined || value < 0) {
    return '';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index++;
  }
  const digits = index === 0 || size >= 100 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(digits)} ${units[index]}`;
}

export function formatMediaResolution(width: number | null | undefined, height: number | null | undefined): string {
  if (!width || !height) {
    return '';
  }
  return `${width} \u00d7 ${height}`;
}

export function formatMediaBitrate(value: number | null | undefined): string {
  if (!value || value <= 0) {
    return '';
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)} Mbps`;
  }
  return `${Math.round(value / 1000)} Kbps`;
}
