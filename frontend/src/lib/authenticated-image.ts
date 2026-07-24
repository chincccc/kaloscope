import type { ActionReturn } from 'svelte/action';

export type AuthenticatedImageRequest = {
  key: string | number;
  load: (signal: AbortSignal) => Promise<Blob>;
};

/** Load an authenticated image without allowing stale requests or broken Blob URLs to flash. */
export function authenticatedImage(
  node: HTMLImageElement,
  initialRequest: AuthenticatedImageRequest
): ActionReturn<AuthenticatedImageRequest> {
  let objectUrl: string | null = null;
  let controller: AbortController | null = null;
  let requestedKey: string | number | null = null;
  let requestVersion = 0;

  async function load(request: AuthenticatedImageRequest) {
    if (requestedKey === request.key) return;
    requestedKey = request.key;
    const version = ++requestVersion;
    controller?.abort();
    const requestController = new AbortController();
    controller = requestController;
    let nextObjectUrl: string | null = null;

    try {
      const blob = await request.load(requestController.signal);
      if (version !== requestVersion) return;

      nextObjectUrl = URL.createObjectURL(blob);
      const preview = new Image();
      preview.src = nextObjectUrl;
      await preview.decode();
      if (version !== requestVersion) {
        URL.revokeObjectURL(nextObjectUrl);
        return;
      }

      const previousObjectUrl = objectUrl;
      objectUrl = nextObjectUrl;
      nextObjectUrl = null;
      node.src = objectUrl;
      if (previousObjectUrl) {
        requestAnimationFrame(() => URL.revokeObjectURL(previousObjectUrl));
      }
    } catch (error) {
      if (nextObjectUrl) URL.revokeObjectURL(nextObjectUrl);
      if (version === requestVersion && (error as Error).name !== 'AbortError') {
        requestedKey = null;
      }
    } finally {
      if (controller === requestController) controller = null;
    }
  }

  void load(initialRequest);
  return {
    update(request) {
      void load(request);
    },
    destroy() {
      requestVersion++;
      controller?.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    }
  };
}
