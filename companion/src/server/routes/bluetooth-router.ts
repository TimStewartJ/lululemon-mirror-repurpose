import { Router } from 'express';
import type { BluetoothProvisioningAdapter } from '../bluetooth-adapter';

/**
 * Exposes the Bluetooth adapter's status so the UI can show "not available
 * yet" rather than a broken control. No provisioning endpoints are wired
 * up until a real adapter (server-mediated BLE or a browser Web Bluetooth
 * flow) replaces NotImplementedBluetoothAdapter.
 */
export function createBluetoothRouter(adapter: BluetoothProvisioningAdapter): Router {
  const router = Router();

  router.get('/bluetooth/status', (_req, res) => {
    res.json(adapter.status());
  });

  return router;
}
