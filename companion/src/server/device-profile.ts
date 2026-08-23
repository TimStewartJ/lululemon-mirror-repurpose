/**
 * Mirrors the machine-readable hardware profile at
 * tools/device-profiles/ifc6309-mirror-329.json in the repository root.
 * The companion is a self-contained deliverable, so the fields it needs
 * for exact ADB device validation are duplicated here rather than read
 * from outside the companion/ directory at runtime. Keep this in sync if
 * the root profile changes.
 */
export interface SupportedDeviceProfile {
  id: string;
  product: string;
  device: string;
  buildId: string;
  fingerprint: string;
}

export const SUPPORTED_DEVICE_PROFILE: SupportedDeviceProfile = {
  id: 'ifc6309-mirror-329',
  product: 'mirror',
  device: 'msm8916_64',
  buildId: 'IFC-6309-2.0-MIR',
  fingerprint: 'mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys',
};

export interface DeviceProperties {
  [prop: string]: string;
}

export interface ProfileValidationResult {
  valid: boolean;
  reasons: string[];
}

/**
 * Validates a device's `getprop` output against the exact supported
 * hardware profile. All of product, device, build ID, and fingerprint must
 * match; any mismatch is reported so the UI can explain what was rejected
 * and why, without ever attempting to "loosely" match new hardware.
 */
export function validateDeviceProfile(
  props: DeviceProperties,
  profile: SupportedDeviceProfile = SUPPORTED_DEVICE_PROFILE,
): ProfileValidationResult {
  const reasons: string[] = [];

  const product = props['ro.product.name'] ?? props['ro.build.product'] ?? '';
  const device = props['ro.product.device'] ?? '';
  const buildId = props['ro.build.id'] ?? '';
  const fingerprint = props['ro.build.fingerprint'] ?? '';

  if (product !== profile.product) {
    reasons.push(`product "${product || '(unknown)'}" does not match required "${profile.product}"`);
  }
  if (device !== profile.device) {
    reasons.push(`device "${device || '(unknown)'}" does not match required "${profile.device}"`);
  }
  if (buildId !== profile.buildId) {
    reasons.push(`build ID "${buildId || '(unknown)'}" does not match required "${profile.buildId}"`);
  }
  if (fingerprint !== profile.fingerprint) {
    reasons.push(`fingerprint does not match the required build fingerprint`);
  }

  return { valid: reasons.length === 0, reasons };
}
